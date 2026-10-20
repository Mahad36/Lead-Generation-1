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
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func
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
WAITING_FOR_INBOX = "Waiting: no eligible inbox can send right now (see Email setup)"
WAITING_FOR_SPACING = "Waiting: spacing emails out (sends in a few minutes)"
WAITING_FOR_SCHEDULE = "Scheduled: sends at the chosen time in the lead's time zone"

# Gmail answers "421 4.4.5 Server busy" when one inbox sends emails (especially with attachments) seconds apart.
MIN_SEND_GAP_SECONDS = 120


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


def effective_daily_limit(account: EmailAccount, today: date | None = None, use_recommended: bool = True) -> int:
    """Today's limit: your own for the account, lower while a new inbox ramps up, and lower again when its health
    calls for it (account_health.assess sets recommended_limit; 0 = paused)."""
    limit = account.daily_limit or 0
    if account.ramp_started_on and limit:
        days = max(((today or local_today()) - account.ramp_started_on).days, 0)
        limit = min(limit, RAMP_START + RAMP_STEP * days)
    recommended = getattr(account, "recommended_limit", None)
    if use_recommended and recommended is not None:
        limit = min(limit, recommended) if limit else recommended
    return limit


def remaining_today(db: Session, account: EmailAccount) -> int:
    from app.services.gmail import sent_today_count

    limit = effective_daily_limit(account)
    if not limit:
        return 10**6  # no limit set
    return max(limit - sent_today_count(db, account), 0)


def account_ready(account: EmailAccount | None, now: datetime | None = None) -> bool:
    """Connected, not paused, not in a backoff / restriction window, login working."""
    if not account or account.status != "connected" or account.paused_reason:
        return False
    if getattr(account, "health_status", "") == "reconnect":
        return False
    until = getattr(account, "restricted_until", None)
    return not (until and until > (now or datetime.now(UTC)))


def pick_sending_account(
    db: Session, user_id: str, allowed_ids: list[str] | None = None, mode: str = "auto", exclude: set[str] | None = None
) -> EmailAccount | None:
    """Inbox rotation, among the inboxes that are safe to send from (send_protection: health and provider checks)
    and have safe capacity left today:

      1. an inbox that may send right now (its rotation delay is over) before one that is still waiting; among those
         waiting, the one that is free soonest
      2. then by mode: "auto" / "selected" the most safe capacity left (bigger inboxes take more), "health" healthier
         first, "even" take turns (the inbox that sent longest ago)
      3. then the inbox that sent longest ago, so equal inboxes alternate

    allowed_ids limits it to the accounts chosen for a campaign / file. When every safe inbox is full, one is
    returned anyway so the caller's capacity check holds the email (rotation never moves emails to get round a
    limit). None when no inbox is safe to send from.
    """
    from app.services.account_health import STATUS_RANK
    from app.services.send_protection import eligible_accounts, ready_at

    safe = [(a, v) for a, v in eligible_accounts(db, user_id, allowed_ids, exclude) if not v.safety_block]
    if not safe:
        return None
    now = datetime.now(UTC)
    with_room = [(a, v, max(ready_at(db, a, now), now)) for a, v in safe if v.ok]
    if not with_room:
        return max((a for a, _ in safe), key=lambda a: a.is_default)

    def room(v) -> int:
        return v.as_dict()["remaining_safe"] if v.safe_capacity is not None else 10**6

    def last_used(a) -> datetime:
        return a.next_send_at or datetime.min.replace(tzinfo=UTC)

    if mode == "even":
        key = lambda e: (e[2], last_used(e[0]))  # noqa: E731
    elif mode == "health":
        key = lambda e: (e[2], STATUS_RANK.get(e[0].health_status, 1), -room(e[1]), last_used(e[0]))  # noqa: E731
    else:
        key = lambda e: (e[2], -room(e[1]), last_used(e[0]))  # noqa: E731
    return min(with_room, key=key)[0]


def reassignment_target(
    db: Session, account: EmailAccount, verdict, user_id: str, allowed_ids: list[str] | None = None, mode: str = "auto"
) -> EmailAccount | None:
    """Another inbox for an email whose inbox is held for its health: only one that independently passes every
    check with safe capacity left. Never for a Gmail restriction (quota, backoff, suspension, pause) or a full day:
    moving those emails elsewhere would be getting round Gmail, so they wait."""
    from app.services.send_protection import evaluate

    if verdict.stage != "health":
        return None
    alternative = pick_sending_account(db, user_id, allowed_ids, mode, exclude={account.id})
    return alternative if alternative and evaluate(db, alternative).ok else None


def held_reasons(db: Session, user_id: str, allowed_ids: list[str] | None = None) -> list[str]:
    """Why each inbox that may be used is held by account health protection (empty: none is)."""
    from app.services.send_protection import eligible_accounts

    return [v.reason for _, v in eligible_accounts(db, user_id, allowed_ids) if v.safety_block]


# ---------------------------------------------------------------- spacing between emails


def seconds_since_last_send(db: Session, account: EmailAccount) -> float | None:
    last = db.query(func.max(EmailMessage.sent_at)).filter(EmailMessage.email_account_id == account.id).scalar()
    return (datetime.now(UTC) - last).total_seconds() if last else None


def sent_recently(db: Session, account: EmailAccount) -> bool:
    """This inbox sent less than MIN_SEND_GAP_SECONDS ago: automatic emails wait a little."""
    since = seconds_since_last_send(db, account)
    return since is not None and since < MIN_SEND_GAP_SECONDS


def require_send_gap(db: Session, account: EmailAccount) -> None:
    if sent_recently(db, account):
        raise SendDeferred(
            f"{account.email_address} sent an email under {MIN_SEND_GAP_SECONDS // 60} minutes ago; "
            "this one goes out in a few minutes.",
            WAITING_FOR_SPACING,
        )


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


# ---------------------------------------------------------------- schedule per scrape file / campaign
#
# A file or campaign can have its own schedule instead of your sending hours:
#   {"mode": "local", "days": [1, 2, 3], "start": 9, "end": 11, "start_at": "2026-10-20T09:00:00+05:00"}
# "local" = those days and hours in each lead's own time zone (from its country and state / city);
# "global" (or no schedule) = your sending hours from AI & settings. start_at = nothing goes out before then.


def lead_timezone(lead) -> str:
    from app.services.markets import timezone_for

    if lead is None:
        return ""
    return timezone_for(lead.country or "", f"{lead.city or ''} {lead.address or ''}")


def _in_days_window(days: list[int], start: int, end: int, local: datetime) -> bool:
    end = end % 24
    if start == end:
        inside, started_on = True, local
    elif start < end:
        inside, started_on = start <= local.hour < end, local
    elif local.hour >= start:
        inside, started_on = True, local
    else:
        inside, started_on = local.hour < end, local - timedelta(days=1)  # the night belongs to the day it began
    return inside and started_on.weekday() in (days or [0, 1, 2, 3, 4])


def _start_at(schedule: dict | None) -> datetime | None:
    raw = (schedule or {}).get("start_at")
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return value if value.tzinfo else value.astimezone()


SLOTS_ENDED = timedelta(days=3650)  # every chosen date and time has passed: wait until new ones are added


def _slot_zone(schedule: dict, tz: str):
    """Chosen dates and times are in your time ("mine") or in each lead's own time ("lead")."""
    if schedule.get("slots_zone") == "lead" and tz:
        return ZoneInfo(tz)
    try:
        return ZoneInfo(schedule["my_timezone"]) if schedule.get("my_timezone") else None
    except Exception:
        return None


def _slot_times(slot: dict, zone) -> tuple[datetime, datetime] | None:
    try:
        day = date.fromisoformat(str(slot["date"]))
        (sh, sm), (eh, em) = (map(int, str(slot[k]).split(":")) for k in ("start", "end"))
    except (KeyError, ValueError):
        return None

    def at(h: int, m: int) -> datetime:
        moment = datetime(day.year, day.month, day.day, tzinfo=zone) + timedelta(hours=h, minutes=m)
        return moment if zone else moment.astimezone()  # no zone: this computer's time

    return at(sh, sm), at(eh, em)


def next_slot_time(schedule: dict, tz: str, probe: datetime) -> datetime:
    """Now (the probe) inside a chosen window, else the start of the next one; far ahead when all have passed."""
    zone = _slot_zone(schedule, tz)
    upcoming = []
    for slot in schedule.get("slots") or []:
        times = _slot_times(slot, zone)
        if not times or times[1] <= probe:
            continue
        if times[0] <= probe:
            return probe
        upcoming.append(times[0])
    return min(upcoming) if upcoming else probe + SLOTS_ENDED


def next_send_time(db: Session, user_id: str, schedule: dict | None, tz: str) -> datetime:
    """When an automatic email under this schedule may go out (now when it may go now)."""
    now = datetime.now(UTC)
    start_at = _start_at(schedule)
    probe = max(now, start_at) if start_at else now
    if schedule and schedule.get("mode") == "now":
        return probe  # send now: no hours restriction (daily limits and spacing still apply)
    if schedule and schedule.get("mode") == "slots":
        return next_slot_time(schedule, tz, probe)
    if not schedule or schedule.get("mode") != "local" or not tz:
        settings = _settings(db, user_id)
        local = probe.astimezone()
        return next_window_open(settings, local)
    zone = ZoneInfo(tz)
    local = probe.astimezone(zone)
    days, start, end = schedule.get("days") or [1, 2, 3], int(schedule.get("start", 9)), int(schedule.get("end", 11))
    if _in_days_window(days, start, end, local):
        return local
    cursor = local.replace(minute=0, second=0, microsecond=0)
    for _ in range(24 * 8):
        cursor += timedelta(hours=1)
        if _in_days_window(days, start, end, cursor):
            return cursor
    return local


def schedule_uses_lead_time(schedule: dict | None) -> bool:
    """Is this schedule in each lead's own time zone (so the lead's time zone is needed)?"""
    mode = (schedule or {}).get("mode")
    return mode == "local" or (mode == "slots" and (schedule or {}).get("slots_zone") == "lead")


def send_time_ok(db: Session, user_id: str, schedule: dict | None, tz: str) -> bool:
    return next_send_time(db, user_id, schedule, tz) <= datetime.now(UTC) + timedelta(seconds=1)


def require_send_time(db: Session, user_id: str, schedule: dict | None, lead) -> None:
    """Automatic sends: wait for the file's / campaign's schedule (in the lead's time), else your sending hours."""
    if schedule and schedule.get("mode") == "now" and not schedule.get("start_at"):
        return  # send now
    if not schedule or (schedule.get("mode") not in ("local", "now", "slots") and not schedule.get("start_at")):
        require_send_window(db, user_id)
        return
    tz = lead_timezone(lead) if schedule_uses_lead_time(schedule) else ""
    opens = next_send_time(db, user_id, schedule, tz)
    if opens - datetime.now(UTC) > SLOTS_ENDED - timedelta(days=1):
        raise SendDeferred(
            "Scheduled: all the dates and times you chose have passed. Add a new date to send this email.",
            WAITING_FOR_SCHEDULE,
        )
    if opens > datetime.now(UTC) + timedelta(seconds=1):
        where = f" ({opens:%a %H:%M} their time)" if tz else ""
        mine = opens.astimezone()
        raise SendDeferred(
            f"Scheduled: this email goes out {mine:%a %d %b %H:%M} your time{where}.",
            WAITING_FOR_SCHEDULE,
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
    from app.utils.emails import is_plausible_email

    if not is_plausible_email(email):
        # Template placeholders, the website company's own support address, a domain-for-sale page: never the business
        return (f"{email} isn't the business's own address (a website template placeholder or the website "
                "company's address), so it would bounce or reach the wrong people.")
    if domain_accepts_mail(email) is False:
        return f"The email domain of {email} doesn't accept mail (no mail server), so it would bounce."
    return ""



def sending_account(
    db: Session,
    user_id: str,
    chosen_id: str | None,
    *,
    automatic: bool,
    allowed_ids: list[str] | None = None,
    mode: str = "auto",
) -> EmailAccount:
    """The inbox that sends this email: the chosen one, or (none chosen) the rotation's pick among allowed_ids.

    Account health protection comes first: a chosen inbox that isn't safe to send from hands an automatic email to
    another inbox only when that one is independently safe and has safe capacity left (logged as a reassignment);
    otherwise the email is held (WAITING_FOR_HEALTH). A full inbox is never swapped for another (that would be
    getting round a limit): its capacity hold comes from send_protection.guard(). A manual send says why it can't go.
    """
    from app.services.campaigns import resolve_email_account
    from app.services.send_protection import WAITING_FOR_HEALTH, evaluate, record_reassignment

    if chosen_id:
        account = resolve_email_account(db, user_id, chosen_id)
        verdict = evaluate(db, account)
        if not verdict.safety_block:
            return account
        if not automatic:
            raise HTTPException(
                status_code=400,
                detail=f"{WAITING_FOR_HEALTH}: {verdict.reason} Sending from this inbox is held to protect it; "
                "choose another inbox in Email setup, or wait until the warning clears.",
            )
        alternative = reassignment_target(db, account, verdict, user_id, allowed_ids, mode)
        if alternative:
            record_reassignment(account, alternative, verdict.reason, "automatic email")
            return alternative
        raise SendDeferred(
            f"{WAITING_FOR_HEALTH}: {verdict.reason} No other inbox is safe to send from with room left, so the "
            "email waits.",
            WAITING_FOR_HEALTH,
        )
    account = pick_sending_account(db, user_id, allowed_ids, mode)
    if account:
        return account
    resolve_email_account(db, user_id, None)  # no connected inbox at all: "Connect Gmail first"
    reasons = held_reasons(db, user_id, allowed_ids)
    message = (
        f"{WAITING_FOR_HEALTH}: no inbox is safe to send from right now ({' / '.join(reasons)}). "
        "The emails wait and go out once an inbox passes every check; see Email health."
        if reasons
        else "No eligible inbox can send right now (paused, restricted or needing reconnection). "
        "The emails wait; see Email setup."
    )
    if automatic:
        raise SendDeferred(message, WAITING_FOR_HEALTH if reasons else WAITING_FOR_INBOX)
    raise HTTPException(status_code=400, detail=message)


def limit_reached_error(account: EmailAccount) -> SendDeferred:
    from app.services.website_audit import WAITING_FOR_LIMIT

    ramp = " (it is still ramping up as a new inbox)" if effective_daily_limit(account) < (account.daily_limit or 0) else ""
    return SendDeferred(
        f"Daily sending limit reached for {account.email_address} ({effective_daily_limit(account)} today{ramp}). "
        "It resets at midnight; change it in AI & settings → Sending, or add another Gmail.",
        WAITING_FOR_LIMIT,
    )
