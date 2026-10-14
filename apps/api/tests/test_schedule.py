from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services import deliverability
from app.services.deliverability import SendDeferred, _in_days_window, lead_timezone, next_send_time
from app.services.markets import best_time, plan, suggested_cities, timezone_for


def test_time_zones_by_country_and_state():
    assert timezone_for("United States", "Miami, Florida") == "America/New_York"
    assert timezone_for("United States", "Seattle, WA") == "America/Los_Angeles"
    assert timezone_for("United States", "Washington, District of Columbia") == "America/New_York"
    assert timezone_for("Australia", "Perth, Western Australia") == "Australia/Perth"
    assert timezone_for("Australia", "New South Wales") == "Australia/Sydney"
    assert timezone_for("Canada", "Calgary, Alberta") == "America/Edmonton"
    assert timezone_for("United Kingdom", "London") == "Europe/London"
    assert timezone_for("Atlantis", "Somewhere") == ""
    lead = SimpleNamespace(country="Australia", city="Brisbane, Queensland", address="")
    assert lead_timezone(lead) == "Australia/Brisbane"


def test_best_time_respects_the_working_week():
    assert best_time("Australia")["days"] == [1, 2, 3]  # Tue-Thu
    assert best_time("Saudi Arabia")["days"] == [6, 0, 1]  # Sun-Tue (Sunday-Thursday week)
    rows = plan("Australia", ["Sydney, New South Wales", "Perth, Western Australia"])["zones"]
    assert {r["timezone"] for r in rows} == {"Australia/Sydney", "Australia/Perth"}


def test_suggested_cities():
    us = suggested_cities("United States")
    assert any("High-paying clients" in c["tags"] for c in us) and any("Most businesses" in c["tags"] for c in us)
    assert all("Florida" in c["area"] for c in suggested_cities("United States", "Florida"))
    assert suggested_cities("Atlantis") == []


def test_days_window_in_local_time():
    sydney = ZoneInfo("Australia/Sydney")
    tuesday_10 = datetime(2026, 10, 13, 10, 0, tzinfo=sydney)
    assert _in_days_window([1, 2, 3], 9, 11, tuesday_10)
    assert not _in_days_window([1, 2, 3], 9, 11, tuesday_10.replace(hour=12))
    assert not _in_days_window([1, 2, 3], 9, 11, tuesday_10 - timedelta(days=1))  # Monday


def test_next_send_time_is_in_the_leads_own_time(monkeypatch):
    schedule = {"mode": "local", "days": [1, 2, 3], "start": 9, "end": 11, "start_at": None}
    opens = next_send_time(None, "u", schedule, "Australia/Sydney")
    local = opens.astimezone(ZoneInfo("Australia/Sydney"))
    assert local.weekday() in (1, 2, 3) and 9 <= local.hour < 11


def test_start_at_holds_emails_until_then(monkeypatch):
    later = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    schedule = {"mode": "local", "days": [0, 1, 2, 3, 4, 5, 6], "start": 0, "end": 24, "start_at": later}
    lead = SimpleNamespace(country="United Kingdom", city="London", address="")
    with pytest.raises(SendDeferred) as caught:
        deliverability.require_send_time(None, "u", schedule, lead)
    assert caught.value.step == deliverability.WAITING_FOR_SCHEDULE
    schedule["start_at"] = None
    deliverability.require_send_time(None, "u", schedule, lead)  # every day, all day: sends now


def test_suggestions_for_every_country():
    for country in ("Vietnam", "Kenya", "Peru", "Pakistan", "Norway"):
        rows = suggested_cities(country)
        assert len(rows) >= 5, country
        assert any("Most businesses" in r["tags"] for r in rows)
    assert suggested_cities("Vietnam")[0]["area"].startswith("Ho Chi Minh")


def test_send_now_ignores_hours():
    lead = SimpleNamespace(country="Australia", city="Sydney", address="")
    deliverability.require_send_time(None, "u", {"mode": "now", "days": [], "start": 9, "end": 11, "start_at": None}, lead)
    assert next_send_time(None, "u", {"mode": "now"}, "") <= datetime.now(UTC) + timedelta(seconds=1)


def _slot(day_offset: int, start: str, end: str, zone=None) -> dict:
    day = (datetime.now(zone or UTC) + timedelta(days=day_offset)).date().isoformat()
    return {"date": day, "start": start, "end": end}


def test_chosen_dates_and_times():
    lead = SimpleNamespace(country="United Kingdom", city="London", address="")
    london = ZoneInfo("Europe/London")
    now = datetime.now(london)
    window_now = {"date": now.date().isoformat(), "start": "00:00", "end": "24:00"}
    schedule = {"mode": "slots", "slots": [window_now], "slots_zone": "lead", "my_timezone": "Asia/Karachi"}
    deliverability.require_send_time(None, "u", schedule, lead)  # inside today's window: sends now

    later = [_slot(3, "13:00", "15:00", london), _slot(2, "18:30", "19:00", london)]
    schedule["slots"] = later
    opens = next_send_time(None, "u", schedule, "Europe/London").astimezone(london)
    assert (opens.date() - now.date()).days == 2 and (opens.hour, opens.minute) == (18, 30)  # the earliest window
    with pytest.raises(SendDeferred) as caught:
        deliverability.require_send_time(None, "u", schedule, lead)
    assert caught.value.step == deliverability.WAITING_FOR_SCHEDULE

    schedule["slots_zone"] = "mine"  # the same times, in Karachi
    opens = next_send_time(None, "u", schedule, "Europe/London").astimezone(ZoneInfo("Asia/Karachi"))
    assert (opens.hour, opens.minute) == (18, 30)

    schedule["slots"] = [_slot(-2, "09:00", "11:00", london)]  # every date has passed
    with pytest.raises(SendDeferred) as caught:
        deliverability.require_send_time(None, "u", schedule, lead)
    assert "passed" in str(caught.value)


def test_schedule_slots_validation():
    from pydantic import ValidationError

    from app.schemas.scraping import ScheduleIn

    with pytest.raises(ValidationError):
        ScheduleIn(mode="slots", slots=[])
    with pytest.raises(ValidationError):
        ScheduleIn(mode="slots", slots=[{"date": "2026-10-20", "start": "15:00", "end": "13:00"}])
    s = ScheduleIn(
        mode="slots",
        slots=[{"date": "2026-10-22", "start": "09:00", "end": "11:00"}, {"date": "2026-10-20", "start": "13:00", "end": "24:00"}],
    ).as_dict()
    assert [x["date"] for x in s["slots"]] == ["2026-10-20", "2026-10-22"] and s["slots_zone"] == "mine"
