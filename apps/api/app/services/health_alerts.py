"""Email health alerts: warns you when something suggests sending could hurt an inbox or your domain.

Every deliverability round (5 minutes) each connected inbox and each own domain is checked. Every signal becomes
at most one open alert (by key); it is resolved by itself once the signal clears. New and worse alerts pop up in the
app (and as a desktop notification) until you've seen them.

Signals
  per inbox    health status (reduced / paused / reconnect), bounce rate, unsubscribe rate, Gmail blocking
               messages, warm-up emails landing in spam, the spam placement test, a daily limit above the safe
               range for cold email, many emails with no replies at all
  per domain   SPF / DKIM / DMARC records (daily), domain blacklists Spamhaus DBL / SURBL / URIBL (daily),
               total daily limit across the domain's inboxes
@gmail.com addresses have no domain checks: Google signs them and their reputation is per address.
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models import EmailAccount, EmailMessage, HealthAlert, WarmupMessage

logger = get_logger("health_alerts")

SAFE_DAILY_PER_INBOX = 40  # cold email per inbox; above this, spam complaints add up quickly
SAFE_DAILY_PER_DOMAIN = 100  # all of an own domain's inboxes together
BOUNCE_WARN, BOUNCE_CRITICAL, BOUNCE_MIN = 0.02, 0.05, 20
MIN_BOUNCES = 2  # an inbox needs at least this many bounces before its bounce rate raises an alert
UNSUB_WARN, UNSUB_CRITICAL, UNSUB_MIN = 0.01, 0.02, 50
SPAM_WARN, SPAM_CRITICAL, SPAM_MIN = 0.10, 0.25, 5  # share of warm-up emails that landed in spam (7 days)
NO_REPLY_MIN = 60  # emails in 14 days without a single reply
DOMAIN_CHECK_EVERY = timedelta(hours=24)
FREE_MAIL = {"gmail.com", "googlemail.com"}

# Free DNS blacklists for domains (the IPs are Google's, so IP lists don't apply to Gmail / Workspace sending).
# A code in `ignore` means the list refused or failed the query: unknown, not listed.
BLACKLISTS = (
    ("Spamhaus DBL", "dbl.spamhaus.org", lambda ip: ip.startswith("127.0.1.")),
    ("SURBL", "multi.surbl.org", lambda ip: ip.startswith("127.0.0.") and ip != "127.0.0.1"),
    ("URIBL", "multi.uribl.com", lambda ip: ip.startswith("127.0.0.") and ip not in ("127.0.0.1", "127.0.0.255")),
)

SLOW_DOWN = "Lower this inbox's daily limit or pause it for a week, and check the leads you're sending to."


def _domain(account: EmailAccount) -> str:
    return account.email_address.rsplit("@", 1)[-1].lower()


def _alert(key: str, kind: str, severity: str, subject: str, title: str, message: str, advice: str, account_id=None):
    return {
        "key": key, "kind": kind, "severity": severity, "subject": subject, "title": title,
        "message": message, "advice": advice, "email_account_id": account_id,
    }


# ---------------------------------------------------------------- per inbox


def inbox_alerts(db: Session, account: EmailAccount, policy: str = "strict") -> tuple[list[dict], set[str]]:
    """(alerts, keys checked) for one connected inbox. policy: your health protection setting (for the wording)."""
    a, who = account.id, account.email_address
    keys = {f"{k}:{a}" for k in ("status", "bounces", "unsubscribes", "blocked", "warmup_spam", "placement", "volume", "no_replies")}
    found: list[dict] = []
    signals = account.health_signals or {}

    status = account.health_status or ""
    if status in ("reduced", "paused", "reconnect"):
        strict = policy != "balanced"
        severity = "warning" if status == "reduced" else "critical"
        title = {
            # Strict protection holds a "reduced" inbox entirely; Balanced halves its sending
            "reduced": "Sending paused to protect this inbox" if strict else "Sending was halved to protect this inbox",
            "paused": "Sending is paused on this inbox",
            "reconnect": "This inbox needs reconnecting",
        }[status]
        advice = {
            "reduced": (
                "Nothing is sent from this inbox while the warning lasts (Strict protection). Check the reasons; if "
                "you've dealt with them, \"I've reviewed this\" lets it send at half speed."
                if strict else
                "The app halved this inbox's sending by itself. Leave it lower for 1-2 weeks; check the reasons."
            ),
            "paused": "Nothing is sent from it until the cause clears. Don't send from it manually in the meantime.",
            "reconnect": "Replace its App Password in Email setup. Nothing is sent from it until then.",
        }[status]
        found.append(_alert(f"status:{a}", "status", severity, who, title, account.health_reason or "", advice, a))

    bounced, total = signals.get("bounces_in_last") or [0, 0]
    rate = bounced / total if total else 0
    if total >= BOUNCE_MIN and bounced >= MIN_BOUNCES and rate >= BOUNCE_WARN:  # one bounce alone isn't a pattern
        found.append(_alert(
            f"bounces:{a}", "bounces", "critical" if rate >= BOUNCE_CRITICAL else "warning", who,
            f"High bounce rate: {rate:.0%}",
            f"{bounced} of the last {total} emails bounced. Above 2% tells Gmail the list is poor quality.",
            "Only send to addresses found on the business's own website; avoid guessed or old addresses. "
            "Bounced addresses are already blocked from being emailed again.",
            a,
        ))

    out, total = signals.get("unsubscribes_in_last") or [0, 0]
    rate = out / total if total else 0
    if total >= UNSUB_MIN and rate >= UNSUB_WARN:
        found.append(_alert(
            f"unsubscribes:{a}", "unsubscribes", "critical" if rate >= UNSUB_CRITICAL else "warning", who,
            f"Many recipients asked not to be emailed: {rate:.0%}",
            f"{out} of the last {total} recipients opted out. People who don't want the email are the ones who "
            "click \"Report spam\", and Gmail doesn't tell you when they do.",
            "Target more closely (business type, places) and make the first lines more specific to the business. "
            + SLOW_DOWN,
            a,
        ))

    if signals.get("rejected_7d"):
        found.append(_alert(
            f"blocked:{a}", "blocked", "critical", who,
            "Gmail blocked messages as against its policy",
            f"Gmail refused {signals['rejected_7d']} message(s) from this inbox in the last 7 days. "
            "This is Gmail's clearest warning before limiting an account.",
            "Pause this inbox for a few days, then restart at a low volume. Check the email for anything spam-like "
            "(many links, link shorteners, salesy wording).",
            a,
        ))

    since = datetime.now(UTC) - timedelta(days=7)
    landed = dict(
        db.query(WarmupMessage.landed, func.count(WarmupMessage.id))
        .filter(WarmupMessage.from_account_id == a, WarmupMessage.sent_at >= since, WarmupMessage.landed.in_(["inbox", "promotions", "spam"]))
        .group_by(WarmupMessage.landed)
        .all()
    )
    checked = sum(landed.values())
    spam = landed.get("spam", 0)
    if checked >= SPAM_MIN and spam / checked >= SPAM_WARN:
        share = spam / checked
        found.append(_alert(
            f"warmup_spam:{a}", "warmup_spam", "critical" if share >= SPAM_CRITICAL else "warning", who,
            f"{share:.0%} of warm-up emails landed in spam",
            f"{spam} of {checked} warm-up emails from this inbox to your other inboxes went to Spam this week. "
            "Leads' inboxes are likely doing the same.",
            "Reduce this inbox's daily limit until warm-up emails land in the Inbox again (the warm-up keeps "
            "rescuing them from spam, which helps). " + SLOW_DOWN,
            a,
        ))

    placement = (account.health or {}).get("placement") or {}
    try:
        recent = placement.get("at") and datetime.fromisoformat(placement["at"]) >= datetime.now(UTC) - timedelta(days=14)
    except ValueError:
        recent = False
    in_spam = [r["to"] for r in placement.get("results") or [] if r.get("landed") == "spam"]
    if recent and not placement.get("running") and in_spam:
        found.append(_alert(
            f"placement:{a}", "placement", "critical", who,
            "Your outreach email landed in spam in the placement test",
            f"The test copy of your latest outreach email went to Spam at {', '.join(in_spam)}.",
            "Lower this inbox's volume, make sure the email has one link at most, and run the test again in a few "
            "days (Email setup -> Inbox health -> spam test).",
            a,
        ))

    if (account.daily_limit or 0) > SAFE_DAILY_PER_INBOX:
        found.append(_alert(
            f"volume:{a}", "volume", "warning", who,
            f"Daily limit {account.daily_limit} is above the safe range",
            f"Cold email above about {SAFE_DAILY_PER_INBOX} a day per inbox makes spam reports add up fast.",
            "Set 25-30 a day in AI & settings -> Sending, and add another inbox if you need more volume.",
            a,
        ))

    two_weeks = datetime.now(UTC) - timedelta(days=14)
    base = db.query(EmailMessage).filter(
        EmailMessage.email_account_id == a, EmailMessage.is_test.is_(False), EmailMessage.sent_at >= two_weeks
    )
    sent = base.count()
    if sent >= NO_REPLY_MIN and not base.filter(EmailMessage.replied_at.isnot(None)).count():
        found.append(_alert(
            f"no_replies:{a}", "no_replies", "warning", who,
            f"No replies to {sent} emails in 2 weeks",
            "Zero replies usually means the emails aren't reaching the inbox, or don't interest the people "
            "who get them. Replies are also the strongest signal Gmail uses to trust an inbox.",
            "Run the spam placement test for this inbox, and review the targeting and the first lines of the email.",
            a,
        ))
    return found, keys


# ---------------------------------------------------------------- per own domain


DOMAIN_BOUNCE_WARN, DOMAIN_BOUNCE_CRITICAL, DOMAIN_BOUNCE_MIN = 0.02, 0.05, 20


def domain_bounce_stats(db: Session, accounts: list[EmailAccount]) -> dict:
    """Emails sent from all of a domain's inboxes in the last 30 days, and how many bounced."""
    since = datetime.now(UTC) - timedelta(days=30)
    sent, bounced = (
        db.query(func.count(EmailMessage.id), func.count(EmailMessage.bounced_at))
        .filter(
            EmailMessage.email_account_id.in_([a.id for a in accounts]),
            EmailMessage.sent_at >= since,
            EmailMessage.is_test.is_(False),
        )
        .one()
    )
    return {"sent": sent or 0, "bounced": bounced or 0, "rate": (bounced or 0) / sent if sent else 0.0}


def domain_overview(db: Session, user_id: str) -> list[dict]:
    """Per own domain (not gmail.com): its inboxes, the last 30 days' sending and bounce rate."""
    by_domain: dict[str, list[EmailAccount]] = {}
    for account in db.query(EmailAccount).filter(EmailAccount.user_id == user_id, EmailAccount.status != "disconnected"):
        if _domain(account) not in FREE_MAIL:
            by_domain.setdefault(_domain(account), []).append(account)
    return [
        {
            "domain": domain,
            "inboxes": sorted(a.email_address for a in accounts),
            **domain_bounce_stats(db, accounts),
            "target": DOMAIN_BOUNCE_WARN,
        }
        for domain, accounts in sorted(by_domain.items())
    ]


def blacklist_listings(domain: str) -> list[str] | None:
    """Lists the domain is on; None when no list could be asked."""
    listed, answered = [], 0
    for name, zone, is_listed in BLACKLISTS:
        try:
            ip = socket.gethostbyname(f"{domain}.{zone}")
        except socket.gaierror:
            answered += 1  # not listed
            continue
        except OSError:
            continue
        if is_listed(ip):
            listed.append(name)
            answered += 1
    return listed if answered else None


def _due(stamp: str | None) -> bool:
    try:
        return not stamp or datetime.fromisoformat(stamp) < datetime.now(UTC) - DOMAIN_CHECK_EVERY
    except ValueError:
        return True


def domain_alerts(db: Session, domain: str, accounts: list[EmailAccount], force: bool = False) -> tuple[list[dict], set[str]]:
    from app.services.gmail import _smtp_payload
    from app.services.warmup import domain_check

    found: list[dict] = []
    keys: set[str] = {f"volume_domain:{domain}"}
    first = accounts[0]

    record = (first.health or {}).get("domain") or {}
    if force or _due(record.get("at")):
        record = domain_check(db, first)
    checks = {c["name"]: c for c in record.get("checks") or []}
    if checks and not record.get("summary", "").startswith("Couldn't reach DNS"):
        keys |= {f"dns_{n.lower()}:{domain}" for n in ("SPF", "DKIM", "DMARC")}
        on_google = any(((_smtp_payload(x) or {}).get("host") or "").endswith("gmail.com") for x in accounts)
        for name, severity in (("SPF", "critical"), ("DKIM", "critical"), ("DMARC", "warning")):
            check = checks.get(name)
            if not check or check["ok"] or (name == "DKIM" and not on_google):
                continue  # DKIM is only checked at Google's selector, so only judged for Google-hosted mail
            found.append(_alert(
                f"dns_{name.lower()}:{domain}", "dns", severity, domain,
                f"{name} is missing for {domain}",
                f"Without {name}, Gmail and Outlook can't confirm your emails really come from {domain}, so more "
                "of them go to spam.",
                check.get("detail", ""),
            ))

    stamp_key = "blacklist"
    stored = (first.health or {}).get(stamp_key) or {}
    if force or _due(stored.get("at")):
        listings = blacklist_listings(domain)
        if listings is not None:
            stored = {"at": datetime.now(UTC).isoformat(), "listed": listings}
            first.health = {**(first.health or {}), stamp_key: stored}
            db.commit()
    if stored.get("at"):
        keys.add(f"blacklist:{domain}")
        if stored.get("listed"):
            found.append(_alert(
                f"blacklist:{domain}", "blacklist", "critical", domain,
                f"{domain} is on a spam blacklist",
                f"Listed on: {', '.join(stored['listed'])}. Mail servers that use these lists send email from or "
                f"linking to {domain} to spam, or reject it.",
                "Stop cold outreach from this domain now. Find the reason and request removal on the list's own "
                "website (free); outreach can resume a few weeks after removal.",
            ))

    keys.add(f"domain_bounces:{domain}")
    stats = domain_bounce_stats(db, accounts)
    if stats["sent"] >= DOMAIN_BOUNCE_MIN and stats["rate"] >= DOMAIN_BOUNCE_WARN:
        found.append(_alert(
            f"domain_bounces:{domain}", "domain_bounces",
            "critical" if stats["rate"] >= DOMAIN_BOUNCE_CRITICAL else "warning", domain,
            f"{domain} bounce rate: {stats['rate']:.1%}",
            f"{stats['bounced']} of the {stats['sent']} emails sent from {domain} in the last 30 days bounced. Gmail "
            "and Outlook judge the whole domain, not just each inbox; under 2% is the safe zone.",
            "Send to business types with active websites (dentists, med spas, independent jewellers), skip chains, "
            "keep 5-10 a day per inbox, and consider turning on email verification for risky leads "
            "(AI & settings -> Sending).",
        ))

    total = sum(x.daily_limit or 0 for x in accounts)
    if len(accounts) > 1 and total > SAFE_DAILY_PER_DOMAIN:
        found.append(_alert(
            f"volume_domain:{domain}", "volume", "warning", domain,
            f"{total} emails a day across {domain}",
            f"{len(accounts)} inboxes on {domain} can send {total} a day in total. Gmail judges the whole domain, so a "
            "bad day affects every address on it, including the ones you use for clients.",
            f"Keep cold outreach from {domain} under about {SAFE_DAILY_PER_DOMAIN} a day, or move the extra volume "
            "to a separate outreach domain.",
        ))
    return found, keys


# ---------------------------------------------------------------- keeping alerts up to date


def check_user(db: Session, user_id: str, force: bool = False) -> dict:
    from app.services.send_protection import policy_for

    policy = policy_for(db, user_id)
    accounts = db.query(EmailAccount).filter(EmailAccount.user_id == user_id).all()
    found: list[dict] = []
    checked: set[str] = set()
    domains: dict[str, list[EmailAccount]] = {}
    for account in accounts:
        if account.status == "disconnected":
            continue  # you switched it off: its alerts are resolved below
        try:
            alerts, keys = inbox_alerts(db, account, policy)
            found += alerts
            checked |= keys
        except Exception as exc:
            db.rollback()
            logger.warning("inbox_alerts_failed", account_id=account.id, error=str(exc)[:200])
        if _domain(account) not in FREE_MAIL:
            domains.setdefault(_domain(account), []).append(account)
    for domain, members in domains.items():
        try:
            alerts, keys = domain_alerts(db, domain, members, force=force)
            found += alerts
            checked |= keys
        except Exception as exc:
            db.rollback()
            logger.warning("domain_alerts_failed", domain=domain, error=str(exc)[:200])
    return save(db, user_id, found, checked, {a.id for a in accounts if a.status == "disconnected"})


def save(db: Session, user_id: str, found: list[dict], checked: set[str], switched_off: set[str] = frozenset()) -> dict:
    """Open / update / re-open the found alerts; resolve checked ones that cleared."""
    now = datetime.now(UTC)
    existing = {a.key: a for a in db.query(HealthAlert).filter(HealthAlert.user_id == user_id)}
    opened = 0
    for item in found:
        alert = existing.get(item["key"])
        if alert is None:
            alert = HealthAlert(user_id=user_id, **item, last_seen_at=now)
            db.add(alert)
            opened += 1
            continue
        worse = alert.severity == "warning" and item["severity"] == "critical"
        if alert.resolved_at or worse:
            alert.resolved_at = alert.notified_at = alert.read_at = alert.acknowledged_at = None  # back / worse: again
            alert.created_at = now
            opened += 1
        for field in ("kind", "severity", "subject", "title", "message", "advice", "email_account_id"):
            setattr(alert, field, item[field])
        alert.last_seen_at = now
    keys = {item["key"] for item in found}
    resolved = 0
    for key, alert in existing.items():
        if alert.resolved_at or key in keys:
            continue
        if key in checked or alert.email_account_id in switched_off:
            alert.resolved_at = now
            resolved += 1
    db.commit()
    return {"open": len(keys), "new": opened, "resolved": resolved}


def run_all(db: Session) -> int:
    """Every user with an inbox (the scheduler's deliverability round)."""
    users = [u for (u,) in db.query(EmailAccount.user_id).distinct()]
    for user_id in users:
        try:
            check_user(db, user_id)
        except Exception as exc:
            db.rollback()
            logger.warning("health_alerts_failed", user_id=user_id, error=str(exc)[:200])
    return len(users)


def as_dict(alert: HealthAlert) -> dict:
    return {
        "id": alert.id,
        "kind": alert.kind,
        "severity": alert.severity,
        "subject": alert.subject,
        "title": alert.title,
        "message": alert.message,
        "advice": alert.advice,
        "email_account_id": alert.email_account_id,
        "since": alert.created_at.isoformat() if alert.created_at else None,
        "last_seen_at": alert.last_seen_at.isoformat() if alert.last_seen_at else None,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
        "read": bool(alert.read_at),
        "notified": bool(alert.notified_at),
        "acknowledged": bool(getattr(alert, "acknowledged_at", None)),
        "holds_sending": holds_sending(alert),
    }


def holds_sending(alert: HealthAlert) -> bool:
    """Does this open alert hold sending under the strict policy (account health protection)?"""
    from app.services.send_protection import HOLDS

    if alert.resolved_at or (alert.severity == "warning" and getattr(alert, "acknowledged_at", None)):
        return False
    kind = alert.key.split(":", 1)[0] if alert.key.startswith("dns_") else alert.kind
    return f"{kind}:{alert.severity}" in HOLDS["strict"]


