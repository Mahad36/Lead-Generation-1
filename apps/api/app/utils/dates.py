"""Calendar days in the local time of the machine running the app (the user's own PC on Windows), so daily limits
reset at the user's midnight rather than at UTC midnight."""

from __future__ import annotations

from datetime import date, datetime, timedelta


def local_day_start() -> datetime:
    """Start of today in local time, as a timezone-aware datetime (comparable with stored UTC timestamps)."""
    return datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)


def local_today() -> date:
    return datetime.now().astimezone().date()


def seconds_until_local_midnight() -> int:
    tomorrow = local_day_start() + timedelta(days=1)
    return max(int((tomorrow - datetime.now().astimezone()).total_seconds()), 60)
