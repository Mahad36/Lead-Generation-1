"""Local risk check of a recipient address, enforced right before every first email and every follow-up.

What it looks at: the address's format, this app's own sending history, and the email domain's public DNS (does the
domain exist, does it publish a mail server, does that server still have an address, is the domain parked or a
throwaway mail service). Nothing else: no verification service, no proxy, and nothing is sent to or asked of the
recipient's mail server (no port 25).

What it is NOT: mailbox verification. DNS and format checks can't tell whether a mailbox exists, so a passed check is
called "low risk" and never "verified" / "valid".

Fail closed: an address is emailed only when every check passed, and passed within FRESH_FOR. A failed check
blocks it; a check that couldn't answer (DNS didn't respond, the domain's DNS is broken, no mail server published) or a
result that is too old holds it, and nothing is sent.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models import AddressCheck, EmailMessage

logger = get_logger("address_check")

LOW_RISK, BLOCKED, HELD = "low_risk", "blocked", "held"
FRESH_FOR = timedelta(hours=24)  # an older result is stale: the address is checked again before it is emailed
LABEL = "Local risk check (address format, sending history and domain DNS). This is not mailbox verification."
# Kinds of hold that are about the moment, not the address: the round stops and the address is checked again later
DNS_UNAVAILABLE = "dns_unavailable"
STALE = "stale"
RETRY_LATER = "Held for now: "  # starts every hold reason that clears by itself once DNS answers again

# RFC 5321/5322 "dot-atom" address with a real-looking domain (letters-only top-level domain); lower-cased first
SYNTAX = re.compile(
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


@dataclass
class Result:
    status: str
    reason: str = ""
    kind: str = ""
    checks: list[dict] = field(default_factory=list)
    checked_at: datetime | None = None

    @property
    def low_risk(self) -> bool:
        return self.status == LOW_RISK

    @property
    def retry_later(self) -> bool:
        """Held only because DNS didn't answer this time (or the result was too old to trust)."""
        return self.status == HELD and self.kind in (DNS_UNAVAILABLE, STALE)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "kind": self.kind,
            "checks": self.checks,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "label": LABEL,
        }


class AddressHeld(Exception):
    """The address didn't pass the local risk check: nothing is sent to it."""

    def __init__(self, email: str, result: Result) -> None:
        super().__init__(result.reason)
        self.email = email
        self.result = result


def _step(checks: list[dict], name: str, outcome: str, detail: str = "") -> None:
    checks.append({"check": name, "outcome": outcome, "detail": detail})


def _stop(checks: list[dict], status: str, kind: str, reason: str) -> Result:
    return Result(status=status, reason=reason, kind=kind, checks=checks)


def _dns_unavailable(checks: list[dict], what: str) -> Result:
    return _stop(
        checks, HELD, DNS_UNAVAILABLE,
        f"{RETRY_LATER}the DNS lookup of {what} didn't answer, so the address couldn't be checked. Nothing is sent "
        "until it can be.",
    )


def run_checks(email: str) -> Result:
    """The address-and-DNS checks, in order; the first one that doesn't pass decides. No database, no state."""
    from app.services.deliverability import DISPOSABLE_DOMAINS, PARKING_MX, dns_query
    from app.utils.emails import is_plausible_email

    address = (email or "").strip().lower()
    checks: list[dict] = []
    local, _, domain = address.rpartition("@")
    if not SYNTAX.match(address) or len(address) > 254 or len(local) > 64:
        _step(checks, "format", "fail", "not a well-formed email address")
        return _stop(checks, BLOCKED, "format", f"{email or 'The address'} isn't a well-formed email address.")
    _step(checks, "format", "pass")

    if domain in DISPOSABLE_DOMAINS:
        _step(checks, "throwaway_domain", "fail", domain)
        return _stop(checks, BLOCKED, "disposable", f"{address} is a throwaway email address, not a business inbox.")
    _step(checks, "throwaway_domain", "pass")

    if not is_plausible_email(address):
        _step(checks, "business_address", "fail", "placeholder, image name or website company's address")
        return _stop(
            checks, BLOCKED, "not_business",
            f"{address} isn't the business's own address (a website template placeholder or the website company's "
            "address), so it would bounce or reach the wrong people.",
        )
    _step(checks, "business_address", "pass")

    mx = dns_query(domain, "MX")
    if mx is None:
        _step(checks, "domain_exists", "unknown", "DNS didn't answer")
        return _dns_unavailable(checks, domain)
    if mx.get("Status") == 3:  # NXDOMAIN
        _step(checks, "domain_exists", "fail", "the domain doesn't exist")
        return _stop(
            checks, BLOCKED, "no_domain",
            f"The email domain of {address} doesn't exist (no mail server), so it would bounce.",
        )
    if mx.get("Status") not in (0, None):
        _step(checks, "domain_exists", "unknown", f"DNS error {mx.get('Status')}")
        return _stop(
            checks, HELD, "dns_error",
            f"{domain}'s DNS returns an error, so it can't be told whether it receives mail. Held, not sent.",
        )
    _step(checks, "domain_exists", "pass")

    records = [r for r in mx.get("Answer") or [] if r.get("type") == 15]
    if records and all(str(r.get("data", "")).strip() in ("0 .", "0") for r in records):
        _step(checks, "mail_server", "fail", "null MX: the domain accepts no mail")
        return _stop(
            checks, BLOCKED, "null_mx",
            f"The email domain of {address} says it accepts no mail (no mail server), so it would bounce.",
        )
    hosts = [
        str(r.get("data", "")).split()[-1].rstrip(".").lower()
        for r in sorted(records, key=lambda r: int((str(r.get("data", "0")).split() or ["0"])[0] or 0))
    ]
    hosts = [h for h in hosts if h and h != "."]
    if not hosts:
        # No MX: senders fall back to the domain's own (website) address, which rarely takes email. Not proven bad,
        # not shown to be good either: held.
        _step(checks, "mail_server", "unknown", "no MX record published")
        return _stop(
            checks, HELD, "no_mx",
            f"{domain} publishes no mail server (MX record), so delivery to {address} is uncertain. Held, not sent.",
        )
    _step(checks, "mail_server", "pass", ", ".join(hosts[:3]))

    if any(p in " ".join(hosts) for p in PARKING_MX):
        _step(checks, "not_parked", "fail", "mail goes to a domain-parking service")
        return _stop(checks, BLOCKED, "parked", f"{domain} is a parked or for-sale domain: nobody reads its mail.")
    _step(checks, "not_parked", "pass")

    unanswered = False
    for host in hosts[:3]:
        for rtype, code in (("A", 1), ("AAAA", 28)):
            found = dns_query(host, rtype)
            if found is None:
                unanswered = True
                continue
            if any(r.get("type") == code for r in found.get("Answer") or []):
                _step(checks, "mail_server_address", "pass", host)
                return Result(status=LOW_RISK, checks=checks)
    if unanswered:
        _step(checks, "mail_server_address", "unknown", "DNS didn't answer")
        return _dns_unavailable(checks, f"{domain}'s mail server")
    _step(checks, "mail_server_address", "fail", "the mail servers have no address")
    return _stop(
        checks, BLOCKED, "dead_mx",
        f"{domain} names a mail server that no longer exists, so mail to {address} can't be delivered (it would "
        "bounce).",
    )


def _bounced_before(db: Session, user_id: str, address: str) -> bool:
    return bool(
        db.query(EmailMessage.id)
        .filter(
            EmailMessage.user_id == user_id,
            func.lower(EmailMessage.recipient_email) == address,
            EmailMessage.bounced_at.isnot(None),
        )
        .first()
    )


def _stored(db: Session, user_id: str, address: str) -> AddressCheck | None:
    return db.query(AddressCheck).filter(AddressCheck.user_id == user_id, AddressCheck.email == address).first()


def _save(db: Session, user_id: str, address: str, result: Result) -> None:
    values = {
        "status": result.status, "reason": result.reason[:2000], "kind": result.kind, "checks": result.checks,
        "checked_at": result.checked_at,
    }
    db.execute(
        insert(AddressCheck)
        .values(user_id=user_id, email=address, **values)
        .on_conflict_do_update(constraint="uq_address_check_user_email", set_={**values, "updated_at": func.now()})
    )
    db.commit()


def check(db: Session, user_id: str, email: str, *, max_age: timedelta = FRESH_FOR,
          now: datetime | None = None) -> Result:
    """The address's local risk check: the stored result while it is fresh, else checked again now (and stored)."""
    now = now or datetime.now(UTC)
    address = (email or "").strip().lower()
    if address and _bounced_before(db, user_id, address):
        # Always looked up live: a bounce since the last check overrides any earlier result
        result = Result(
            status=BLOCKED, kind="bounced_before", checked_at=now,
            reason=f"An earlier email to {address} bounced, so it isn't emailed again.",
            checks=[{"check": "sending_history", "outcome": "fail", "detail": "bounced before"}],
        )
        _save(db, user_id, address, result)
        return result
    stored = _stored(db, user_id, address) if address else None
    if stored and stored.checked_at and now - stored.checked_at <= max_age and stored.kind not in (DNS_UNAVAILABLE, STALE):
        return Result(stored.status, stored.reason, stored.kind, list(stored.checks or []), stored.checked_at)
    result = run_checks(address)
    result.checks.insert(0, {"check": "sending_history", "outcome": "pass", "detail": "no earlier bounce"})
    result.checked_at = now
    if address:
        _save(db, user_id, address, result)
    if not result.low_risk:
        logger.info("address_check_not_low_risk", email=address, status=result.status, kind=result.kind)
    return result


def enforce(db: Session, user_id: str, email: str, *, now: datetime | None = None) -> Result:
    """Right before sending: a low-risk result no older than FRESH_FOR, or AddressHeld (nothing is sent)."""
    result = check(db, user_id, email, now=now)
    if not result.low_risk:
        raise AddressHeld(email, result)
    if not result.checked_at or (now or datetime.now(UTC)) - result.checked_at > FRESH_FOR:
        # Can't happen through check() (it re-checks stale results), kept as the last line: never trust an old pass
        raise AddressHeld(email, Result(HELD, f"{RETRY_LATER}the last check of {email} is too old.", STALE))
    return result


def low_risk_only(db: Session, user_id: str, emails: list[str]) -> list[str]:
    """The addresses (e.g. CC'd colleagues) that pass the check now; the others are left out of the email."""
    kept = []
    for address in emails:
        if check(db, user_id, address).low_risk:
            kept.append(address)
        else:
            logger.info("cc_left_out_by_address_check", email=address)
    return kept


def latest(db: Session, user_id: str, email: str) -> dict | None:
    """The stored result, for showing on a lead (without checking again)."""
    stored = _stored(db, user_id, (email or "").strip().lower())
    if not stored:
        return None
    return Result(stored.status, stored.reason, stored.kind, list(stored.checks or []), stored.checked_at).as_dict()
