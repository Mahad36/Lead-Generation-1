"""Deliverability rules shared by every sender (audit emails, campaigns, follow-ups).

- Inbox rotation: when no account is chosen, each email goes from the connected inbox with the most room left today.
- Ramp-up: an inbox added after this feature starts at RAMP_START a day and rises by RAMP_STEP a day to its limit.
- Bounce auto-pause: an inbox whose recent emails bounce too often stops sending until you resume it.
- Sending hours: automatic sends wait for the window (e.g. 8:00-19:00, Monday-Friday, in your local time).
- Address check: an email domain that can't receive mail (no MX / A record) is skipped before any work is done.
"""

from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models import EmailAccount, EmailMessage, UserSettings
from app.utils.dates import local_today

logger = get_logger("deliverability")

RAMP_START = 5  # emails on a new inbox's first day
RAMP_STEP = 2  # more each day after that
BOUNCE_WINDOW = 50  # look at this many recent emails
BOUNCE_MIN_SENT = 20  # ...once at least this many were sent
BOUNCE_PAUSE_RATE = 0.05  # pause above 5% bounces

WAITING_FOR_WINDOW = "Waiting: outside sending hours (sends automatically when they start)"
WAITING_FOR_INBOX = "Waiting: the sending inbox is paused (too many bounces). Resume it in Email setup."


class SendDeferred(HTTPException):
    """An automatic send that has to wait (daily limit, sending hours, paused inbox); `step` says why."""

    def __init__(self, detail: str, step: str) -> None:
        super().__init__(status_code=429, detail=detail)
        self.step = step


def waiting_step(exc: Exception, default: str) -> str:
    return getattr(exc, "step", "") or default


def _settings(db: Session, user_id: str) -> UserSettings | None:
    return db.query(UserSettings).filter(UserSettings.user_id == user_id).first()


# ---------------------------------------------------------------- daily limits, ramp-up, rotation


def effective_daily_limit(account: EmailAccount, today: date | None = None) -> int:
    """Today's limit: the account's own, lower while a new inbox ramps up."""
    limit = account.daily_limit or 0
    if account.ramp_started_on and limit:
        days = max(((today or local_today()) - account.ramp_started_on).days, 0)
        limit = min(limit, RAMP_START + RAMP_STEP * days)
    return limit


def remaining_today(db: Session, account: EmailAccount) -> int:
    from app.services.gmail import sent_today_count

    limit = effective_daily_limit(account)
    if not limit:
        return 10**6  # no limit set
    return max(limit - sent_today_count(db, account), 0)


def account_ready(account: EmailAccount | None) -> bool:
    return bool(account and account.status == "connected" and not account.paused_reason)


def pick_sending_account(db: Session, user_id: str) -> EmailAccount | None:
    """Inbox rotation: the connected, unpaused inbox with the most room today (ties: the default account).

    When every inbox is full, the one that would have been used is returned anyway, so the caller's daily-limit
    check holds the email until tomorrow.
    """
    accounts = [
        a
        for a in db.query(EmailAccount).filter(EmailAccount.user_id == user_id, EmailAccount.status == "connected")
        if not a.paused_reason
    ]
    if not accounts:
        return None
    ranked = sorted(accounts, key=lambda a: (remaining_today(db, a), a.is_default), reverse=True)
    return ranked[0]


# ---------------------------------------------------------------- bounce auto-pause


def check_bounce_rate(db: Session, account: EmailAccount) -> bool:
    """Pause the inbox when too many of its recent emails bounced. Returns True when it was paused now."""
    if account.paused_reason:
        return False
    recent = (
        db.query(EmailMessage.bounced_at)
        .filter(
            EmailMessage.email_account_id == account.id,
            EmailMessage.sent_at.isnot(None),
            EmailMessage.is_test.is_(False),
        )
        .order_by(EmailMessage.sent_at.desc())
        .limit(BOUNCE_WINDOW)
        .all()
    )
    if len(recent) < BOUNCE_MIN_SENT:
        return False
    bounced = sum(1 for (at,) in recent if at)
    rate = bounced / len(recent)
    if rate <= BOUNCE_PAUSE_RATE:
        return False
    account.paused_reason = (
        f"Paused automatically: {bounced} of the last {len(recent)} emails bounced ({rate:.0%}). Too many bounces "
        "hurt this inbox's reputation. Check the lead emails, then resume it."
    )
    account.paused_at = datetime.now(UTC)
    logger.warning("account_auto_paused", account_id=account.id, bounced=bounced, sent=len(recent))
    return True


# ---------------------------------------------------------------- sending hours


def _now_local() -> datetime:
    return datetime.now().astimezone()


def in_send_window(settings: UserSettings | None, now: datetime | None = None) -> bool:
    """Inside the sending hours. A window across midnight (e.g. 17:00-02:00) belongs to the day it starts on, so
    with Monday-Friday, Friday 17:00 to Saturday 02:00 sends and Sunday night to Monday 02:00 doesn't."""
    if not settings or not settings.send_window_enabled:
        return True
    now = now or _now_local()
    start, end = settings.send_window_start, settings.send_window_end % 24
    if start == end:
        inside, started_on = True, now
    elif start < end:
        inside, started_on = start <= now.hour < end, now
    elif now.hour >= start:
        inside, started_on = True, now
    else:
        inside, started_on = now.hour < end, now - timedelta(days=1)
    if inside and settings.send_weekdays_only and started_on.weekday() >= 5:
        return False
    return inside


def next_window_open(settings: UserSettings | None, now: datetime | None = None) -> datetime:
    """When automatic sending may start again (now when inside the window)."""
    now = now or _now_local()
    if in_send_window(settings, now):
        return now
    probe = now.replace(minute=0, second=0, microsecond=0)
    for _ in range(24 * 8):
        probe += timedelta(hours=1)
        if in_send_window(settings, probe):
            return probe
    return now


def seconds_until_window(db: Session, user_id: str) -> int:
    """0 inside the sending hours, else the seconds until they start."""
    settings = _settings(db, user_id)
    now = _now_local()
    opens = next_window_open(settings, now)
    return max(int((opens - now).total_seconds()), 0)


def window_label(settings: UserSettings | None) -> str:
    if not settings or not settings.send_window_enabled:
        return "any time"
    days = "Mon-Fri" if settings.send_weekdays_only else "every day"
    return f"{settings.send_window_start:02d}:00-{settings.send_window_end % 24:02d}:00, {days}"


def load_day_start_hour(db: Session) -> None:
    """Use the user's "daily limits reset at" hour for every "today" count (app/utils/dates.py)."""
    from app.utils.dates import set_day_start_hour

    row = db.query(UserSettings.day_start_hour).order_by(UserSettings.updated_at.desc()).first()
    set_day_start_hour(row[0] if row else 0)


def require_send_window(db: Session, user_id: str) -> None:
    """For automatic sends: raise SendDeferred outside the sending hours."""
    settings = _settings(db, user_id)
    if not in_send_window(settings):
        opens = next_window_open(settings)
        raise SendDeferred(
            f"Outside your sending hours ({window_label(settings)}); it sends at {opens:%a %H:%M}.",
            WAITING_FOR_WINDOW,
        )


# ---------------------------------------------------------------- address check (does the domain receive mail?)

DOH = ("https://1.1.1.1/dns-query", "https://8.8.8.8/resolve")
_dns_cache: dict[tuple[str, str], tuple[float, dict | None]] = {}
_dns_lock = threading.Lock()


def dns_query(name: str, rtype: str) -> dict | None:
    """A DNS-over-HTTPS JSON answer ({"Status": .., "Answer": [..]}), or None when no resolver answered."""
    key = (name.lower(), rtype)
    now = time.time()
    with _dns_lock:
        hit = _dns_cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    answer = None
    for resolver in DOH:
        try:
            url = f"{resolver}?name={urllib.parse.quote(name)}&type={rtype}"
            request = urllib.request.Request(url, headers={"accept": "application/dns-json"})
            with urllib.request.urlopen(request, timeout=6, context=ssl.create_default_context()) as response:
                answer = json.loads(response.read().decode())
            break
        except Exception:
            continue
    with _dns_lock:
        _dns_cache[key] = (now + (3600 if answer is not None else 60), answer)
    return answer


def domain_accepts_mail(email_or_domain: str) -> bool | None:
    """True / False, or None when DNS couldn't be checked (then the email is not blocked)."""
    domain = email_or_domain.rsplit("@", 1)[-1].strip().lower().rstrip(".")
    if not domain or "." not in domain:
        return False
    mx = dns_query(domain, "MX")
    if mx is None:
        return None
    if mx.get("Status") == 3:  # NXDOMAIN: the domain doesn't exist
        return False
    records = [r for r in mx.get("Answer") or [] if r.get("type") == 15]
    if records:
        # "0 ." is a null MX: the domain says it accepts no mail
        return not all(str(r.get("data", "")).strip() in ("0 .", "0") for r in records)
    a = dns_query(domain, "A")  # no MX: mail goes to the domain's own address, if it has one
    if a is None:
        return None
    return any(r.get("type") == 1 for r in a.get("Answer") or [])


def address_problem(db: Session, user_id: str, email: str) -> str:
    """Why this address shouldn't be emailed ("" when it's fine or the check is switched off)."""
    settings = _settings(db, user_id)
    if settings is not None and not settings.check_email_domains:
        return ""
    if domain_accepts_mail(email) is False:
        return f"The email domain of {email} doesn't accept mail (no mail server), so it would bounce."
    return ""



def sending_account(db: Session, user_id: str, chosen_id: str | None, *, automatic: bool) -> EmailAccount:
    """The inbox that sends this email: the chosen one, or (none chosen) the rotation's pick.

    Automatic sends wait (SendDeferred) when the inbox they need is paused; a manual send says why it can't go.
    """
    from app.services.campaigns import resolve_email_account

    if chosen_id:
        account = resolve_email_account(db, user_id, chosen_id)
        if account.paused_reason:
            message = f"{account.email_address} is paused: {account.paused_reason}"
            if automatic:
                raise SendDeferred(message, WAITING_FOR_INBOX)
            raise HTTPException(status_code=400, detail=f"{message} Resume it in Email setup, or choose another inbox.")
        return account
    account = pick_sending_account(db, user_id)
    if account:
        return account
    resolve_email_account(db, user_id, None)  # no connected inbox at all: "Connect Gmail first"
    message = "Every connected inbox is paused because of bounces. Resume one in Email setup."
    if automatic:
        raise SendDeferred(message, WAITING_FOR_INBOX)
    raise HTTPException(status_code=400, detail=message)


def limit_reached_error(account: EmailAccount) -> SendDeferred:
    from app.services.website_audit import WAITING_FOR_LIMIT

    ramp = " (it is still ramping up as a new inbox)" if effective_daily_limit(account) < (account.daily_limit or 0) else ""
    return SendDeferred(
        f"Daily sending limit reached for {account.email_address} ({effective_daily_limit(account)} today{ramp}). "
        "It resets at midnight; change it in AI & settings → Sending, or add another Gmail.",
        WAITING_FOR_LIMIT,
    )
