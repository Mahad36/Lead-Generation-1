"""Account health protection: the check every outreach email passes immediately before it is sent.

Protect the inbox first; limits come second. A daily limit is a maximum, not a target: an inbox with room left
still doesn't send while there's a credible sign that sending could hurt it.

The decision, in order (the first that fails blocks the inbox, whatever capacity is left):
  1. health        health assessed and recent; no open warning sign that holds sending (see HOLDS)
  2. provider      connected, login working, not paused, no Gmail restriction / backoff / quota pause
  3. safe capacity today's safe capacity (ramp-up for new inboxes, health-based reduction, 80% of Gmail's cap)
  4. your limit    the daily limit you set

What this can and can't see: bounces, unsubscribes, Gmail's own errors and refusals, where warm-up and test emails
land, DNS records and blacklists. Gmail doesn't share a mailbox's reputation or spam-complaint rate, so no
"spam score" is made up: when the health can't be assessed at all, sending is held (fail safe).

Used by every outreach path: audit emails (automatic, retries, held queue, manual sends), campaigns, follow-ups.
Not by replies you write to someone who wrote to you, warm-up between your own inboxes, or test emails.

Atomic: guard() locks the inbox's row until the caller commits its "sending" email record, and emails still being
sent count against capacity and spacing, so two workers can't both take the last slot.

Rotation delay: each inbox waits a random gap (your "delay between emails", e.g. 3-7 minutes; a campaign's own range
for its emails) after every email before the next automatic one. The gap is reserved with the send itself
(next_send_at, under the same lock), so it holds whatever sends next: an audit email, a campaign, a follow-up.
Inboxes on the same own domain also keep DOMAIN_GAP_SECONDS apart, so a domain never sends in bursts.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models import EmailAccount, EmailMessage, HealthAlert, UserSettings

logger = get_logger("send_protection")

WAITING_FOR_HEALTH = "Paused — Account Health Protection"
HEALTH_STALE = timedelta(minutes=20)  # older than this: assessed again before sending
IN_FLIGHT = timedelta(minutes=30)  # an email "sending" for longer than this is stuck, not in flight
DOMAIN_GAP_SECONDS = 60  # between any two emails from inboxes on the same own domain

# Which warning signs hold sending. "strict" (default): any credible sign. "balanced": only serious ones (warnings
# then halve the inbox's capacity instead, as before). Keys are alert kinds, or "kind:severity".
HOLDS = {
    "strict": {
        "status:warning",  # reduced: bounce >= 3%, unsubscribes >= 2%, Gmail refusals or repeated errors
        "bounces:critical",  # >= 5% (2-3% shows as a warning; >= 3% also reduces the status)
        "unsubscribes:warning", "unsubscribes:critical",
        "blocked:critical",
        "warmup_spam:warning", "warmup_spam:critical",
        "placement:critical",
        "blacklist:critical",
        "dns_spf:critical", "dns_dkim:critical",
    },
    "balanced": {
        "bounces:critical", "unsubscribes:critical", "blocked:critical", "warmup_spam:critical",
        "placement:critical", "blacklist:critical", "dns_spf:critical", "dns_dkim:critical",
    },
}
# Paused / reconnect / Gmail restrictions are the provider stage (2), with their exact reason.
POLICIES = tuple(HOLDS)
DEFAULT_POLICY = "strict"


@dataclass
class Verdict:
    ok: bool
    stage: str  # "" when ok | health | provider | safe_capacity | user_limit
    reason: str
    user_limit: int  # 0 = none set
    safe_capacity: int | None  # today's conservative capacity (None = no limit)
    sent_today: int
    in_flight: int
    ready_at: datetime | None = None  # spacing: when this inbox may send next

    @property
    def used(self) -> int:
        return self.sent_today + self.in_flight

    @property
    def safety_block(self) -> bool:
        """Blocked by health or a provider restriction (not just a full day)."""
        return self.stage in ("health", "provider")

    def as_dict(self) -> dict:
        data = asdict(self)
        data["remaining_configured"] = max(self.user_limit - self.used, 0) if self.user_limit else None
        data["remaining_safe"] = max(self.safe_capacity - self.used, 0) if self.safe_capacity is not None else None
        data["eligible"] = self.ok
        data["ready_at"] = self.ready_at.isoformat() if self.ready_at else None
        return data


# ---------------------------------------------------------------- the decision


def policy_for(db: Session, user_id: str) -> str:
    row = db.query(UserSettings.health_policy).filter(UserSettings.user_id == user_id).first()
    return row[0] if row and row[0] in HOLDS else DEFAULT_POLICY


def _domain(account: EmailAccount) -> str:
    return account.email_address.rsplit("@", 1)[-1].lower()


def health_holds(db: Session, account: EmailAccount, policy: str) -> list[str]:
    """The warning signs that hold this inbox now: live from its own signals, plus its domain's open alerts.
    A warning you reviewed (acknowledged) no longer holds; anything critical always does."""
    from app.services.health_alerts import FREE_MAIL, inbox_alerts

    holds = HOLDS.get(policy, HOLDS[DEFAULT_POLICY])
    found, _ = inbox_alerts(db, account, policy)
    domain = _domain(account)
    if domain not in FREE_MAIL:
        found += [
            {"key": a.key, "kind": a.key.split(":", 1)[0], "severity": a.severity, "title": a.title}
            for a in db.query(HealthAlert).filter(
                HealthAlert.user_id == account.user_id,
                HealthAlert.email_account_id.is_(None),
                HealthAlert.subject == domain,
                HealthAlert.resolved_at.is_(None),
            )
        ]
    acknowledged = {
        key
        for (key,) in db.query(HealthAlert.key).filter(
            HealthAlert.user_id == account.user_id,
            HealthAlert.acknowledged_at.isnot(None),
            HealthAlert.resolved_at.is_(None),
            HealthAlert.severity == "warning",
        )
    }
    reasons = []
    for alert in found:
        kind = alert["key"].split(":", 1)[0] if alert["key"].startswith("dns_") else alert["kind"]
        if f"{kind}:{alert['severity']}" not in holds:
            continue
        if alert["severity"] == "warning" and alert["key"] in acknowledged:
            continue
        reasons.append(alert.get("message") or alert["title"] if kind == "status" else alert["title"])
    return reasons


def in_flight_count(db: Session, account: EmailAccount, now: datetime | None = None) -> int:
    since = (now or datetime.now(UTC)) - IN_FLIGHT
    return (
        db.query(func.count(EmailMessage.id))
        .filter(
            EmailMessage.email_account_id == account.id,
            EmailMessage.status == "sending",
            EmailMessage.sent_at.is_(None),
            EmailMessage.created_at >= since,
        )
        .scalar()
        or 0
    )


def decide(
    account: EmailAccount | None,
    *,
    holds: list[str],
    assessed: bool,
    sent_today: int,
    in_flight: int,
    safe_capacity: int | None,
    now: datetime | None = None,
) -> Verdict:
    """The decision itself (pure: every input is given). Order: health, provider, safe capacity, your limit."""
    now = now or datetime.now(UTC)
    user_limit = (account.daily_limit or 0) if account else 0

    def verdict(stage: str, reason: str) -> Verdict:
        return Verdict(not stage, stage, reason, user_limit, safe_capacity, sent_today, in_flight)

    if account is None:
        return verdict("provider", "No email account.")
    who = account.email_address
    # 1. health and safety
    if not assessed:
        return verdict("health", f"{who}: its health couldn't be checked, so it doesn't send until it can.")
    if holds:
        return verdict("health", f"{who}: " + "; ".join(h.rstrip(".") for h in holds) + ".")
    # 2. provider restrictions
    if account.status != "connected" or getattr(account, "health_status", "") == "reconnect":
        return verdict("provider", f"{who}: Gmail login isn't working. Reconnect it in Email setup.")
    if account.paused_reason:
        return verdict("provider", f"{who}: {account.paused_reason}")
    until = getattr(account, "restricted_until", None)
    if until and until > now:
        detail = getattr(account, "health_reason", "") or "Gmail asked to slow down."
        return verdict("provider", f"{who}: {detail} (until {until.astimezone():%a %H:%M}).")
    # 3. effective safe capacity, 4. your limit
    used = sent_today + in_flight
    if safe_capacity is not None and safe_capacity <= 0:
        return verdict("safe_capacity", f"{who}: no safe capacity today.")
    if safe_capacity is not None and used >= safe_capacity and (not user_limit or safe_capacity < user_limit):
        return verdict(
            "safe_capacity",
            f"{who}: reached today's safe capacity ({safe_capacity}, below your limit of {user_limit or 'none'}).",
        )
    if user_limit and used >= user_limit:
        return verdict("user_limit", f"{who}: reached your daily limit ({user_limit}).")
    return verdict("", "")


def refresh_if_stale(db: Session, account: EmailAccount) -> bool:
    """Assess the inbox again when its last assessment is old. False when it can't be assessed (fail safe)."""
    checked = getattr(account, "health_checked_at", None)
    if checked and checked > datetime.now(UTC) - HEALTH_STALE:
        return True
    from app.services.account_health import assess

    try:
        assess(db, account)
        return True
    except Exception as exc:
        db.rollback()
        logger.warning("health_assess_failed", account_id=account.id, error=str(exc)[:200])
        return False


def evaluate(db: Session, account: EmailAccount | None, *, refresh: bool = True, policy: str | None = None) -> Verdict:
    """Can this inbox send one more outreach email right now? (No lock: guard() is the one used to send.)"""
    from app.services.deliverability import effective_daily_limit
    from app.services.gmail import sent_today_count

    if account is None:
        return decide(None, holds=[], assessed=True, sent_today=0, in_flight=0, safe_capacity=None)
    assessed = refresh_if_stale(db, account) if refresh else bool(getattr(account, "health_checked_at", None))
    try:
        holds = health_holds(db, account, policy or policy_for(db, account.user_id)) if assessed else []
    except Exception as exc:  # can't read the signals: treat as not assessed
        db.rollback()
        logger.warning("health_signals_failed", account_id=account.id, error=str(exc)[:200])
        assessed, holds = False, []
    limit = effective_daily_limit(account)
    recommended = getattr(account, "recommended_limit", None)
    safe = 0 if recommended is not None and recommended <= 0 else (limit or None)
    return decide(
        account,
        holds=holds,
        assessed=assessed,
        sent_today=sent_today_count(db, account),
        in_flight=in_flight_count(db, account),
        safe_capacity=safe,
    )


# ---------------------------------------------------------------- sending


class HealthHold(Exception):
    """Raised by guard(); `verdict` says why. Callers turn it into their own "wait" (it is never a failure)."""

    def __init__(self, verdict: Verdict) -> None:
        super().__init__(verdict.reason)
        self.verdict = verdict


def guard(
    db: Session, account: EmailAccount, *, spacing: bool = True, delay: tuple[int, int] | None = None
) -> Verdict:
    """Immediately before sending: re-check everything with the inbox row locked. The lock holds until the caller
    commits the email's "sending" record (which then counts as in flight), so concurrent sends queue up here.
    On a hold the transaction is rolled back (releasing the lock) and HealthHold is raised.

    spacing: wait for this inbox's rotation delay (automatic emails; a manual send goes straight away). Either way
    the next delay is reserved here: next_send_at = now + a random gap from `delay` (default: your settings)."""
    refresh_if_stale(db, account)  # may commit: before taking the lock
    lock(db, account)
    verdict = evaluate(db, account, refresh=False)
    now = datetime.now(UTC)
    if verdict.ok and spacing:
        ready = ready_at(db, account, now)
        if ready > now:
            wait = max(int((ready - now).total_seconds()), 1)
            verdict = Verdict(
                False, "spacing",
                f"{account.email_address} waits between emails; next one in {wait // 60} min {wait % 60} s.",
                verdict.user_limit, verdict.safe_capacity, verdict.sent_today, verdict.in_flight, ready,
            )
    if not verdict.ok:
        db.rollback()  # release the lock before anything else touches this inbox's row
        if verdict.safety_block:
            record_state(account, verdict)
        raise HealthHold(verdict)
    low, high = delay or delay_range(db, account.user_id)
    account.next_send_at = now + timedelta(seconds=random.uniform(*_floor((low, high))))  # committed with the email
    return verdict


def _floor(delay: tuple[int, int]) -> tuple[int, int]:
    """Never under Gmail's minimum gap (back-to-back sends get "421 server busy")."""
    from app.services.deliverability import MIN_SEND_GAP_SECONDS

    low = max(int(delay[0] or 0), MIN_SEND_GAP_SECONDS)
    return low, max(int(delay[1] or 0), low)


def delay_range(db: Session, user_id: str) -> tuple[int, int]:
    """Your delay between two emails from the same inbox (AI & settings -> Sending), in seconds."""
    from app.config import get_settings

    app = get_settings()
    row = (
        db.query(UserSettings.default_min_send_delay_seconds, UserSettings.default_max_send_delay_seconds)
        .filter(UserSettings.user_id == user_id)
        .first()
    )
    low, high = row if row else (app.default_min_send_delay_seconds, app.default_max_send_delay_seconds)
    return _floor((low or app.default_min_send_delay_seconds, high or app.default_max_send_delay_seconds))


def ready_at(db: Session, account: EmailAccount, now: datetime | None = None) -> datetime:
    """When this inbox may send its next automatic email: after its rotation delay, Gmail's minimum gap since its
    last email, a little while when an email is still being sent, and the gap since the last email from any inbox
    on the same own domain."""
    from app.services.deliverability import MIN_SEND_GAP_SECONDS
    from app.services.health_alerts import FREE_MAIL

    now = now or datetime.now(UTC)
    moments = [now]
    if account.next_send_at:
        moments.append(account.next_send_at)
    last = db.query(func.max(EmailMessage.sent_at)).filter(EmailMessage.email_account_id == account.id).scalar()
    if last:
        moments.append(last + timedelta(seconds=MIN_SEND_GAP_SECONDS))
    if in_flight_count(db, account, now):
        moments.append(now + timedelta(seconds=MIN_SEND_GAP_SECONDS))
    domain = _domain(account)
    if domain not in FREE_MAIL:
        siblings = [
            a for (a,) in db.query(EmailAccount.id).filter(
                EmailAccount.user_id == account.user_id,
                EmailAccount.id != account.id,
                EmailAccount.email_address.ilike(f"%@{domain}"),
            )
        ]
        if siblings:
            recent = (
                db.query(func.max(func.coalesce(EmailMessage.sent_at, EmailMessage.created_at)))
                .filter(
                    EmailMessage.email_account_id.in_(siblings),
                    EmailMessage.status.in_(["sending", "sent"]),
                    EmailMessage.created_at >= now - timedelta(hours=1),
                )
                .scalar()
            )
            if recent:
                moments.append(recent + timedelta(seconds=DOMAIN_GAP_SECONDS))
    return max(moments)


def soonest_ready(db: Session, user_id: str, allowed_ids: list[str] | None = None) -> datetime | None:
    """The earliest moment one of these inboxes (safe, with capacity left) may send; None if none can today."""
    now = datetime.now(UTC)
    times = [ready_at(db, a, now) for a, v in eligible_accounts(db, user_id, allowed_ids) if v.ok]
    return min(times) if times else None


def lock(db: Session, account: EmailAccount) -> None:
    db.query(EmailAccount.id).filter(EmailAccount.id == account.id).with_for_update().one_or_none()
    db.refresh(account)


def as_deferred(hold: HealthHold):
    """The matching "wait" for the automatic senders (they retry held emails by themselves)."""
    from app.services.deliverability import WAITING_FOR_SPACING, SendDeferred
    from app.services.website_audit import WAITING_FOR_LIMIT

    v = hold.verdict
    if v.safety_block:
        return SendDeferred(f"{WAITING_FOR_HEALTH}: {v.reason} Emails wait until an inbox is safe to send from.", WAITING_FOR_HEALTH)
    if v.stage == "spacing":
        return SendDeferred(v.reason, WAITING_FOR_SPACING)
    return SendDeferred(f"{v.reason} It resets with the daily limits.", WAITING_FOR_LIMIT)


def eligible_accounts(
    db: Session, user_id: str, allowed_ids: list[str] | None = None, exclude: set[str] | None = None
) -> list[tuple[EmailAccount, Verdict]]:
    """Connected inboxes (of allowed_ids) with their verdicts, safest-to-use first."""
    rows = []
    query = db.query(EmailAccount).filter(EmailAccount.user_id == user_id, EmailAccount.status == "connected")
    for account in query.all():
        if (allowed_ids and account.id not in allowed_ids) or (exclude and account.id in exclude):
            continue
        rows.append((account, evaluate(db, account)))
    return rows


def record_state(account: EmailAccount, verdict: Verdict) -> None:
    """Keep an audit trail of when an inbox stops / starts being allowed to send (only when that changes).
    Never call it while holding the inbox's row lock: it writes the row from its own session."""
    blocked = verdict.safety_block
    previous = (account.health or {}).get("protection") or {}
    if bool(previous.get("blocked")) == blocked and (not blocked or previous.get("reason") == verdict.reason):
        return
    _log_event(
        account,
        "sending.blocked" if blocked else "sending.allowed",
        {"stage": verdict.stage, "reason": verdict.reason} if blocked else {"previous_reason": previous.get("reason", "")},
        {"blocked": blocked, "stage": verdict.stage if blocked else "", "reason": verdict.reason if blocked else "",
         "since": datetime.now(UTC).isoformat()},
    )


def record_reassignment(account_from: EmailAccount, account_to: EmailAccount, reason: str, what: str) -> None:
    _log_event(
        account_from,
        "sending.reassigned",
        {"to_account_id": account_to.id, "to": account_to.email_address, "reason": reason, "email": what},
    )


def protection_round(db: Session) -> int:
    """Scheduler: re-evaluate every inbox, record changes (blocked / allowed again), resume held campaigns."""
    from app.services.campaigns import resume_health_held_campaigns

    changed = 0
    for account in db.query(EmailAccount).filter(EmailAccount.status != "disconnected").all():
        try:
            verdict = evaluate(db, account)
            before = bool(((account.health or {}).get("protection") or {}).get("blocked"))
            db.commit()
            record_state(account, verdict)
            changed += before != verdict.safety_block
        except Exception as exc:
            db.rollback()
            logger.warning("protection_round_failed", account_id=account.id, error=str(exc)[:200])
    resume_health_held_campaigns(db)
    return changed


def _log_event(account: EmailAccount, action: str, details: dict, protection: dict | None = None) -> None:
    """In its own session: the sending code may roll its own transaction back right after."""
    from app.database import get_session
    from app.services.audit import write_audit

    try:
        db = get_session()
    except Exception:
        return
    try:
        write_audit(db, user_id=account.user_id, action=action, entity_type="email_account", entity_id=account.id,
                    details={"account": account.email_address, **details})
        if protection is not None:
            row = db.get(EmailAccount, account.id)
            if row:
                row.health = {**(row.health or {}), "protection": protection}
                account.health = {**(account.health or {}), "protection": protection}
        db.commit()
        logger.info(action, account_id=account.id, **{k: str(v)[:200] for k, v in details.items()})
    except Exception as exc:
        db.rollback()
        logger.warning("protection_log_failed", account_id=account.id, error=str(exc)[:200])
    finally:
        db.close()


# ---------------------------------------------------------------- for the dashboard


def recent_events(db: Session, account_id: str, limit: int = 10) -> list[dict]:
    from app.models import AuditLog

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.entity_id == account_id, AuditLog.action.in_(["sending.blocked", "sending.allowed", "sending.reassigned"]))
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return [{"action": r.action, "at": r.created_at.isoformat(), "details": r.details or {}} for r in rows]


def held_counts(db: Session, user_id: str) -> dict:
    """Emails waiting because of health protection right now."""
    from app.models import Campaign, CampaignLead, WebsiteAudit

    audits = (
        db.query(func.count(WebsiteAudit.id))
        .filter(WebsiteAudit.user_id == user_id, WebsiteAudit.sent_at.is_(None), WebsiteAudit.step == WAITING_FOR_HEALTH)
        .scalar()
        or 0
    )
    campaign_leads = (
        db.query(func.count(CampaignLead.id))
        .join(Campaign, Campaign.id == CampaignLead.campaign_id)
        .filter(
            Campaign.user_id == user_id,
            Campaign.paused_reason == "account_health_protection",
            CampaignLead.status.in_(["queued", "approved", "ready"]),
        )
        .scalar()
        or 0
    )
    return {"audits": audits, "campaign_emails": campaign_leads, "total": audits + campaign_leads}


def reassigned_today(db: Session, account_id: str) -> int:
    from app.models import AuditLog
    from app.utils.dates import local_day_start

    return (
        db.query(func.count(AuditLog.id))
        .filter(AuditLog.entity_id == account_id, AuditLog.action == "sending.reassigned", AuditLog.created_at >= local_day_start())
        .scalar()
        or 0
    )


# ---------------------------------------------------------------- audit budget (Claude tokens)


def audit_budget(db: Session, user_id: str, allowed_ids: list[str] | None = None) -> tuple[int, str]:
    """How many more automatic audits are worth starting now, and why none when 0.

    An audit costs Claude tokens, so it only starts when its email can go out today: the safe capacity the inboxes
    may still send today (health protection and limits included), minus the audited emails already waiting for a
    slot or being audited. When it's used up, audits wait and start again by themselves when the daily limits reset
    (or an inbox becomes safe again)."""
    from app.models import WebsiteAudit
    from app.services.website_audit import ACTIVE_STATUSES

    rows = eligible_accounts(db, user_id, allowed_ids)
    if not rows:
        return 0, "No connected inbox to send from, so no audits are started."
    safe = [(a, v) for a, v in rows if not v.safety_block]
    if not safe:
        return 0, f"{WAITING_FOR_HEALTH}: no inbox is safe to send from, so no audits are started (saves Claude tokens)."
    capacity = 0
    for _, v in safe:
        if not v.ok:
            continue  # full for today
        left = v.as_dict()["remaining_safe"]
        if left is None:
            return 10**6, ""  # an inbox without any limit
        capacity += left
    backlog = (
        db.query(func.count(WebsiteAudit.id))
        .filter(
            WebsiteAudit.user_id == user_id,
            WebsiteAudit.sent_at.is_(None),
            WebsiteAudit.auto_send.is_(True),
            # "sending" is already counted in the inboxes' capacity (in flight)
            WebsiteAudit.status.in_([s for s in (*ACTIVE_STATUSES, "ready") if s != "sending"]),
        )
        .scalar()
        or 0
    )
    budget = capacity - backlog
    if budget > 0:
        return budget, ""
    return 0, (
        f"Today's sending capacity is used up ({capacity} email{'s' if capacity != 1 else ''} left across your inboxes, "
        f"{backlog} audited email{'s' if backlog != 1 else ''} already waiting). New audits pause to save Claude tokens "
        "and start again by themselves when the daily limits reset."
    )
