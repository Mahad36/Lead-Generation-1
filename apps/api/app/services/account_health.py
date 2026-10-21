"""Sending health and capacity of each connected Gmail account, from evidence the app can actually observe.

What is known: Google's documented daily sending limits (about 500 a rolling day for a free @gmail.com account,
2,000 for Google Workspace), every email this app sent and its outcome (bounces, unsubscribe requests, Gmail's
SMTP error responses), the account's own Sent folder over the last 24 hours (so mail sent from Gmail itself counts
too), and whether the login still works.

What is NOT available: Gmail does not share a mailbox's spam-complaint rate, sender reputation or remaining quota
with individual senders (Postmaster Tools only covers whole domains with large volumes). Those are reported as
unavailable; the status is a summary of the evidence above, never a guarantee that Gmail considers the account safe.

Statuses
  healthy    enough history, no warning signs              full recommended capacity
  monitoring new / little history, or early warning signs   full capacity, watched (new inboxes still ramp up)
  reduced    elevated bounces / unsubscribes / errors       half capacity
  paused     Gmail quota hit, repeated errors, too many      nothing sent until the restriction clears
             bounces
  reconnect  login rejected or disconnected                 nothing sent until reconnected
"""

from __future__ import annotations

import imaplib
import re
import smtplib
import socket
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models import EmailAccount, EmailMessage, SuppressionEntry

logger = get_logger("account_health")

# Google's documented limits for Gmail used over SMTP / the API (per rolling 24 hours)
GMAIL_CONSUMER_LIMIT = 500
GOOGLE_WORKSPACE_LIMIT = 2000
CONSERVATIVE_SHARE = 0.8  # never plan above 80% of the provider's maximum

BACKOFF_FIRST_MINUTES = 2
BACKOFF_MAX_MINUTES = 60
QUOTA_PAUSE = timedelta(hours=24)
REJECTION_PAUSE = timedelta(hours=1)
MIN_BOUNCES = 2  # bounce-based warnings need at least this many bounces, not just a rate
SIGNAL_WINDOW = timedelta(days=30)  # bounce and unsubscribe rates look at this period

STATUS_RANK = {"healthy": 0, "monitoring": 1, "reduced": 2, "paused": 3, "reconnect": 4}
STATUS_LABEL = {
    "healthy": "Healthy",
    "monitoring": "Monitoring",
    "reduced": "Reduced sending",
    "paused": "Temporarily paused",
    "reconnect": "Reconnection required",
}
UNAVAILABLE = [
    "Spam-complaint rate: Gmail doesn't share it for individual mailboxes (unsubscribe requests are used instead).",
    "Sender reputation: Gmail keeps it private.",
    "Exact remaining Gmail quota: Gmail doesn't report it; it is estimated from the Sent folder.",
]


# ---------------------------------------------------------------- Gmail's error responses


def classify_send_error(exc: Exception) -> tuple[str, int | None]:
    """(kind, SMTP code) for a failed send.

    temporary     4xx "try again later" / connection problems      retry with backoff
    rate_limited  4.7.x rate / too many connections                retry with longer backoff
    quota         550 5.4.5 daily sending limit exceeded           account paused for 24 hours
    auth          login rejected                                   reconnect needed
    recipient     the address doesn't exist / was refused           never retried, address suppressed
    rejected      5.7.x message blocked (policy / spam)            account slowed, never retried
    other         any other permanent error                        never retried
    """
    code = getattr(exc, "smtp_code", None)
    raw = getattr(exc, "smtp_error", b"")
    text = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")) + " " + str(exc)
    low = text.lower()
    if isinstance(exc, smtplib.SMTPAuthenticationError) or code == 535 or "username and password not accepted" in low:
        return "auth", code
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "recipient", code
    if "5.4.5" in low or "daily user sending" in low or "sending limit exceeded" in low or "quota" in low:
        return "quota", code
    if code and 500 <= code < 600:
        if re.search(r"5\.1\.\d", low) or "does not exist" in low or "user unknown" in low:
            return "recipient", code
        if "5.7." in low:
            return "rejected", code
        return "other", code
    if code and 400 <= code < 500:
        if "4.7." in low or "rate" in low or "too many" in low:
            return "rate_limited", code
        return "temporary", code
    if isinstance(exc, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, socket.timeout, OSError, TimeoutError)):
        return "temporary", code
    return "other", code


RETRYABLE = {"temporary", "rate_limited", "quota"}


def backoff_minutes(failures: int, kind: str = "temporary") -> int:
    """2, 4, 8, 16... minutes (rate limits start at 8), capped at an hour."""
    first = BACKOFF_FIRST_MINUTES * (4 if kind == "rate_limited" else 1)
    return min(first * 2 ** max(failures - 1, 0), BACKOFF_MAX_MINUTES)


def record_send_failure(db: Session, account: EmailAccount, kind: str, detail: str = "") -> None:
    now = datetime.now(UTC)
    if kind in ("temporary", "rate_limited"):
        account.consecutive_failures = (account.consecutive_failures or 0) + 1
        minutes = backoff_minutes(account.consecutive_failures, kind)
        account.restricted_until = now + timedelta(minutes=minutes)
        account.health_reason = (
            f"Gmail asked to slow down ({detail or kind}). Waiting {minutes} min before sending again"
            f" (attempt {account.consecutive_failures})."
        )
    elif kind == "quota":
        account.restricted_until = now + QUOTA_PAUSE
        account.health_status = "paused"
        account.health_reason = "Gmail reported this account's daily sending limit was reached. Paused for 24 hours."
    elif kind == "auth":
        account.health_status = "reconnect"
        account.health_reason = "Gmail rejected the login. Replace the App Password in Email setup."
    elif kind == "rejected":
        account.consecutive_failures = (account.consecutive_failures or 0) + 1
        account.restricted_until = now + REJECTION_PAUSE
        account.health_reason = "Gmail blocked a message as against its policy (possible spam signal). Paused 1 hour."
    db.add(account)
    db.commit()
    logger.warning("send_failure_recorded", account_id=account.id, kind=kind, until=str(account.restricted_until))


def record_send_success(db: Session, account: EmailAccount) -> None:
    changed = False
    if account.consecutive_failures:
        account.consecutive_failures, changed = 0, True
    if account.health_status == "reconnect":
        account.health_status, changed = "monitoring", True  # the login works again
    if changed:
        db.add(account)


# ---------------------------------------------------------------- capacity and status


def provider_limit(account: EmailAccount) -> int | None:
    """Google's documented daily limit for this account type (None when not a Gmail / Workspace account)."""
    from app.services.gmail import _smtp_payload

    domain = account.email_address.rsplit("@", 1)[-1].lower()
    if domain in ("gmail.com", "googlemail.com"):
        return GMAIL_CONSUMER_LIMIT
    host = ((_smtp_payload(account) or {}).get("host") or "").lower()
    if account.provider == "gmail" or "gmail" in host or "google" in host:
        return GOOGLE_WORKSPACE_LIMIT
    return None


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def gather_signals(db: Session, account: EmailAccount) -> dict:
    now = datetime.now(UTC)
    base = db.query(EmailMessage).filter(EmailMessage.email_account_id == account.id, EmailMessage.is_test.is_(False))
    sent = base.filter(EmailMessage.sent_at.isnot(None))
    # Rates over the last 30 days only: an inbox held by health protection sends nothing, so old bounces must be
    # able to age out (otherwise a held inbox could never recover).
    recent = sent.filter(EmailMessage.sent_at >= now - SIGNAL_WINDOW).order_by(EmailMessage.sent_at.desc()).limit(100).all()
    last50 = recent[:50]
    bounced = sum(1 for m in last50 if m.bounced_at)
    suppressed = {
        e for (e,) in db.query(SuppressionEntry.email).filter(
            SuppressionEntry.user_id == account.user_id, ~SuppressionEntry.reason.ilike("%bounce%")
        )
    }
    opted_out = sum(1 for m in recent if (m.recipient_email or "").lower() in suppressed)
    failures = dict(
        db.query(EmailMessage.error_kind, func.count(EmailMessage.id))
        .filter(
            EmailMessage.email_account_id == account.id,
            EmailMessage.status == "failed",
            EmailMessage.created_at >= now - timedelta(hours=24),
        )
        .group_by(EmailMessage.error_kind)
        .all()
    )
    rejected_7d = base.filter(
        EmailMessage.status == "failed", EmailMessage.error_kind == "rejected", EmailMessage.created_at >= now - timedelta(days=7)
    ).count()
    previous = account.health_signals or {}
    return {
        "sent_total": sent.count(),
        "sent_24h_app": sent.filter(EmailMessage.sent_at >= now - timedelta(hours=24)).count(),
        "sent_24h_gmail": previous.get("sent_24h_gmail"),  # from the Sent folder (refresh_sent_24h)
        "sent_24h_gmail_at": previous.get("sent_24h_gmail_at"),
        "bounce_rate": _rate(bounced, len(last50)),
        "bounces_in_last": [bounced, len(last50)],
        "unsubscribe_rate": _rate(opted_out, len(recent)),
        "unsubscribes_in_last": [opted_out, len(recent)],
        "failures_24h": {k or "other": v for k, v in failures.items()},
        "rejected_7d": rejected_7d,
        "provider_limit": provider_limit(account),
        "unavailable": UNAVAILABLE,
    }


def decide(account: EmailAccount, signals: dict, now: datetime | None = None) -> tuple[str, str, float]:
    """(status, reason, capacity factor) from the account's state and signals. Pure: easy to test."""
    now = now or datetime.now(UTC)
    if account.status != "connected" or account.health_status == "reconnect":
        return "reconnect", account.health_reason or "The account is disconnected. Reconnect it in Email setup.", 0.0
    if account.paused_reason:
        return "paused", account.paused_reason, 0.0
    if account.restricted_until and account.restricted_until > now:
        until = account.restricted_until.astimezone().strftime("%a %H:%M")
        return "paused", f"{account.health_reason or 'Temporarily restricted.'} Resumes {until}.", 0.0
    bounce, unsub = signals.get("bounce_rate"), signals.get("unsubscribe_rate")
    bounced_count, sent_bounce = (signals.get("bounces_in_last") or [0, 0])[:2]
    sent_unsub = (signals.get("unsubscribes_in_last") or [0, 0])[1]
    failures = sum((signals.get("failures_24h") or {}).get(k, 0) for k in ("temporary", "rate_limited", "rejected", "quota"))
    limit = signals.get("provider_limit")
    used = max(signals.get("sent_24h_app") or 0, signals.get("sent_24h_gmail") or 0)
    if limit and used >= limit * CONSERVATIVE_SHARE:
        return "paused", f"{used} emails in the last 24 hours, close to Gmail's limit of {limit}. Waits for it to roll over.", 0.0
    reasons = []
    # A single bounce in a young inbox's first emails isn't evidence of a bad list (1 in 20 is already "5%"):
    # bounce-based warnings need at least MIN_BOUNCES bounces as well as the rate
    if sent_bounce >= 20 and bounced_count >= MIN_BOUNCES and bounce is not None and bounce >= 0.03:
        reasons.append(f"bounce rate {bounce:.0%} ({bounced_count} of the last {sent_bounce} emails)")
    if sent_unsub >= 50 and unsub is not None and unsub >= 0.02:
        reasons.append(f"{unsub:.0%} of recent recipients asked not to be emailed")
    if signals.get("rejected_7d"):
        reasons.append(f"Gmail blocked {signals['rejected_7d']} message(s) this week")
    if failures >= 3:
        reasons.append(f"{failures} send errors from Gmail in the last 24 hours")
    if reasons:
        return "reduced", "Warning signs: " + "; ".join(reasons) + ".", 0.5
    watch = []
    if (signals.get("sent_total") or 0) < 20:
        watch.append("not enough sending history yet to judge")
    if sent_bounce >= 20 and bounce is not None and bounce >= 0.02:
        watch.append(f"bounce rate {bounce:.0%}")
    if sent_unsub >= 50 and unsub is not None and unsub >= 0.01:
        watch.append(f"unsubscribe rate {unsub:.0%}")
    if failures:
        watch.append(f"{failures} send error(s) in the last 24 hours")
    if account.ramp_started_on:
        from app.services.deliverability import RAMP_START, effective_daily_limit

        if effective_daily_limit(account, use_recommended=False) < (account.daily_limit or 0):
            watch.append(f"new inbox ramping up from {RAMP_START} a day")
    if watch:
        return "monitoring", "Watching: " + "; ".join(watch) + ".", 1.0
    return "healthy", "No warning signs in recent sending (bounces, unsubscribes and Gmail errors are low).", 1.0


def assess(db: Session, account: EmailAccount) -> dict:
    """Recalculate status and today's recommended capacity, and store them on the account."""
    from app.services.deliverability import effective_daily_limit

    now = datetime.now(UTC)
    signals = gather_signals(db, account)
    status, reason, factor = decide(account, signals, now)
    if account.restricted_until and account.restricted_until <= now:
        account.restricted_until = None  # the restriction has cleared
    base = effective_daily_limit(account, use_recommended=False)  # your limit, lowered while ramping up
    limit = signals.get("provider_limit")
    if limit:
        base = min(base or limit, int(limit * CONSERVATIVE_SHARE))
    account.recommended_limit = 0 if factor == 0 else max(1, int(base * factor)) if base else None
    account.health_status, account.health_reason = status, reason
    account.health_signals = {**signals, "assessed_at": now.isoformat()}
    account.health_checked_at = now
    db.add(account)
    db.commit()
    return health_summary(db, account)


def refresh_sent_24h(db: Session, account: EmailAccount) -> int | None:
    """Emails this Gmail sent in the last 24 hours, counted in its own Sent folder (includes mail sent from Gmail)."""
    from app.services.gmail import _smtp_payload
    from app.services.inbox import _find_sent_folder, imap_host_for

    data = _smtp_payload(account)
    if not data:
        return None
    since = datetime.now(UTC) - timedelta(hours=24)
    count = 0
    imap = imaplib.IMAP4_SSL(imap_host_for(data), int(data.get("imap_port") or 993), timeout=30)
    try:
        imap.login(data.get("username") or account.email_address, data.get("password") or "")
        folder = account.sent_folder or _find_sent_folder(imap)
        if not folder or imap.select(f'"{folder}"', readonly=True)[0] != "OK":
            return None
        typ, found = imap.uid("SEARCH", None, "SINCE", (since - timedelta(days=1)).strftime("%d-%b-%Y"))
        uids = found[0].split() if typ == "OK" and found and found[0] else []
        for i in range(0, len(uids), 300):
            typ, rows = imap.uid("FETCH", b",".join(uids[i : i + 300]).decode(), "(INTERNALDATE)")
            for row in rows or []:
                raw = row if isinstance(row, bytes) else row[0] if isinstance(row, tuple) else b""
                stamp = imaplib.Internaldate2tuple(raw)
                if stamp and datetime(*stamp[:6], tzinfo=datetime.now().astimezone().tzinfo) >= since:
                    count += 1
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    account.health_signals = {
        **(account.health_signals or {}),
        "sent_24h_gmail": count,
        "sent_24h_gmail_at": datetime.now(UTC).isoformat(),
    }
    db.add(account)
    db.commit()
    return count


def assess_all(db: Session) -> int:
    """Every connected account: the Sent-folder count hourly, the status and capacity every round."""
    checked = 0
    for account in db.query(EmailAccount).filter(EmailAccount.status.in_(["connected", "expired"])):
        try:
            stamp = (account.health_signals or {}).get("sent_24h_gmail_at")
            if not stamp or datetime.fromisoformat(stamp) < datetime.now(UTC) - timedelta(hours=1):
                try:
                    refresh_sent_24h(db, account)
                except Exception as exc:  # the Sent folder is a bonus signal
                    db.rollback()
                    logger.info("sent_24h_unavailable", account_id=account.id, error=str(exc)[:160])
            assess(db, account)
            checked += 1
        except Exception as exc:
            db.rollback()
            logger.warning("account_assess_failed", account_id=account.id, error=str(exc)[:200])
    return checked


def health_summary(db: Session, account: EmailAccount) -> dict:
    from app.services.deliverability import effective_daily_limit, remaining_today
    from app.services.gmail import sent_today_count

    signals = account.health_signals or {}
    limit = signals.get("provider_limit")
    used_24h = max(signals.get("sent_24h_app") or 0, signals.get("sent_24h_gmail") or 0)
    return {
        "status": account.health_status,
        "label": STATUS_LABEL.get(account.health_status, account.health_status),
        "reason": account.health_reason,
        "recommended_today": account.recommended_limit,
        "your_daily_limit": account.daily_limit,
        "limit_today": effective_daily_limit(account),
        "sent_today": sent_today_count(db, account),
        "remaining_today": remaining_today(db, account),
        "provider_limit": limit,
        "sent_last_24h": used_24h,
        "sent_last_24h_source": "Sent folder (includes mail sent from Gmail)"
        if signals.get("sent_24h_gmail") is not None
        else "this app only",
        "provider_remaining_estimate": max(limit - used_24h, 0) if limit else None,
        "bounce_rate": signals.get("bounce_rate"),
        "bounces_in_last": signals.get("bounces_in_last"),
        "unsubscribe_rate": signals.get("unsubscribe_rate"),
        "failures_24h": signals.get("failures_24h") or {},
        "restricted_until": account.restricted_until,
        "checked_at": account.health_checked_at,
        "unavailable": UNAVAILABLE,
        "protection": protection_summary(db, account),
    }


def protection_summary(db: Session, account: EmailAccount) -> dict:
    """Account health protection for the dashboard: may it send, why not, what's held, what moved, and when."""
    from app.services import send_protection

    try:
        verdict = send_protection.evaluate(db, account, refresh=False)
    except Exception as exc:
        db.rollback()
        logger.warning("protection_summary_failed", account_id=account.id, error=str(exc)[:200])
        return {"eligible": False, "stage": "health", "reason": "Health couldn't be checked; sending is held."}
    state = (account.health or {}).get("protection") or {}
    low, high = send_protection.delay_range(db, account.user_id)
    slow = send_protection.low_speed(db, account)
    return {
        "low_speed": slow,
        # offered when the only thing holding the inbox is its bounce rate
        "low_speed_available": not slow and verdict.stage == "health" and "bounce" in (verdict.reason or "").lower()
        and not any(w in (verdict.reason or "").lower() for w in ("blocked", "blacklist", "spam", "spf", "dkim")),
        **verdict.as_dict(),
        "next_send_at": send_protection.ready_at(db, account).isoformat(),
        "delay_seconds": [low, high],
        "policy": send_protection.policy_for(db, account.user_id),
        "blocked_since": state.get("since") if state.get("blocked") else None,
        "held": send_protection.held_counts(db, account.user_id),
        "reassigned_today": send_protection.reassigned_today(db, account.id),
        "events": send_protection.recent_events(db, account.id),
    }


# ---------------------------------------------------------------- rotation


def rank_accounts(entries: list[tuple[EmailAccount, int]], mode: str = "auto") -> list[EmailAccount]:
    """Eligible accounts in the order they should send, from (account, remaining capacity today).

    Not a round-robin: an account with more room gets more emails; "health" mode prefers healthier accounts first.
    Accounts that are paused, restricted, need reconnecting, or have no room left are never chosen.
    """
    from app.services.deliverability import account_ready

    eligible = [(a, r) for a, r in entries if account_ready(a) and r > 0]
    if mode == "health":
        key = lambda e: (-STATUS_RANK.get(e[0].health_status, 1), e[1], e[0].is_default)  # noqa: E731
    else:
        key = lambda e: (e[1], e[0].is_default)  # noqa: E731
    return [a for a, _ in sorted(eligible, key=key, reverse=True)]


def suppress_refused_recipient(db: Session, user_id: str, lead_id: str | None, email: str) -> None:
    """Gmail refused the address outright (it doesn't exist): treat it as a hard bounce, never email it again."""
    from app.models import Lead
    from app.services.suppression import add_suppression

    lead = db.get(Lead, lead_id) if lead_id else None
    if lead:
        lead.email_status = "bounced"
    try:
        add_suppression(db, user_id, email, reason="bounced", source="send_refused")
    except Exception as exc:  # already suppressed, etc.
        db.rollback()
        logger.info("suppress_refused_failed", email=email, error=str(exc)[:120])
    db.commit()
