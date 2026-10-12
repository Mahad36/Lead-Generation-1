"""Working days in the local time of the machine running the app (the user's own PC on Windows).

Daily limits reset at the start of the user's working day: midnight by default, or the hour set in
AI & settings -> Sending ("Daily limits reset at", e.g. 17:00 for a 5 pm - 2 am shift). Everything that counts
"today" (emails sent, audits started, warm-up, ramp-up days) uses these helpers.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

_day_start_hour = 0  # set from the user's settings (set_day_start_hour)


def set_day_start_hour(hour: int | None) -> None:
    global _day_start_hour
    _day_start_hour = min(max(int(hour or 0), 0), 23)


def day_start_hour() -> int:
    return _day_start_hour


def local_day_start() -> datetime:
    """Start of the current working day in local time, as a timezone-aware datetime (comparable with UTC)."""
    now = datetime.now().astimezone()
    start = now.replace(hour=_day_start_hour, minute=0, second=0, microsecond=0)
    if now < start:
        start -= timedelta(days=1)  # e.g. 01:00 belongs to the day that started at 17:00 yesterday
    return start


def local_today() -> date:
    """The working day's date (the date it started on)."""
    return local_day_start().date()


def seconds_until_local_midnight() -> int:
    """Seconds until the daily limits reset (the next working day starts)."""
    tomorrow = local_day_start() + timedelta(days=1)
    return max(int((tomorrow - datetime.now().astimezone()).total_seconds()), 60)
