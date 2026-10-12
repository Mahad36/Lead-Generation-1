from datetime import date, datetime, timedelta
from types import SimpleNamespace

from app.services import deliverability
from app.services.deliverability import (
    domain_accepts_mail,
    effective_daily_limit,
    in_send_window,
    next_window_open,
    waiting_step,
    SendDeferred,
)
from app.services.followups import _fallback_body, followup_days


def _account(limit=30, ramp=None):
    return SimpleNamespace(daily_limit=limit, ramp_started_on=ramp)


def test_ramp_up_for_new_inboxes_only():
    today = date(2026, 10, 9)
    assert effective_daily_limit(_account(30), today) == 30  # existing inbox: no ramp
    assert effective_daily_limit(_account(30, today), today) == 5
    assert effective_daily_limit(_account(30, today - timedelta(days=4)), today) == 13
    assert effective_daily_limit(_account(30, today - timedelta(days=40)), today) == 30  # caught up
    assert effective_daily_limit(_account(3, today), today) == 3  # never above the inbox's own limit


def _window(start=8, end=19, weekdays=True, enabled=True):
    return SimpleNamespace(
        send_window_enabled=enabled, send_window_start=start, send_window_end=end, send_weekdays_only=weekdays
    )


def test_sending_hours():
    thursday_noon = datetime(2026, 10, 8, 12, 0)
    thursday_night = datetime(2026, 10, 8, 22, 30)
    saturday_noon = datetime(2026, 10, 10, 12, 0)
    assert in_send_window(_window(), thursday_noon)
    assert not in_send_window(_window(), thursday_night)
    assert not in_send_window(_window(), saturday_noon)
    assert in_send_window(_window(weekdays=False), saturday_noon)
    assert in_send_window(_window(enabled=False), thursday_night)
    assert in_send_window(None, thursday_night)
    # Thursday night -> Friday 08:00; Saturday -> Monday 08:00
    assert next_window_open(_window(), thursday_night) == datetime(2026, 10, 9, 8, 0)
    assert next_window_open(_window(), saturday_noon) == datetime(2026, 10, 12, 8, 0)
    assert next_window_open(_window(), thursday_noon) == thursday_noon


def test_waiting_step():
    assert waiting_step(SendDeferred("x", "step A"), "default") == "step A"
    assert waiting_step(ValueError("x"), "default") == "default"


def test_domain_accepts_mail(monkeypatch):
    answers = {
        ("ok.com", "MX"): {"Status": 0, "Answer": [{"type": 15, "data": "10 mx.ok.com."}]},
        ("nullmx.com", "MX"): {"Status": 0, "Answer": [{"type": 15, "data": "0 ."}]},
        ("gone.com", "MX"): {"Status": 3},
        ("aonly.com", "MX"): {"Status": 0},
        ("aonly.com", "A"): {"Status": 0, "Answer": [{"type": 1, "data": "1.2.3.4"}]},
        ("nothing.com", "MX"): {"Status": 0},
        ("nothing.com", "A"): {"Status": 0},
    }
    monkeypatch.setattr(deliverability, "dns_query", lambda name, rtype: answers.get((name, rtype)))
    assert domain_accepts_mail("owner@ok.com") is True
    assert domain_accepts_mail("owner@nullmx.com") is False
    assert domain_accepts_mail("owner@gone.com") is False
    assert domain_accepts_mail("owner@aonly.com") is True
    assert domain_accepts_mail("owner@nothing.com") is False
    assert domain_accepts_mail("owner@dns-down.com") is None  # unknown: not blocked
    assert domain_accepts_mail("not-an-email") is False


def test_followup_days():
    assert followup_days(None) == [3, 5]
    assert followup_days(SimpleNamespace(followup_days="2, 4 ,7")) == [2, 4, 7]
    assert followup_days(SimpleNamespace(followup_days="1,2,3,4,5,6,7")) == [1, 2, 3, 4, 5]
    assert followup_days(SimpleNamespace(followup_days="")) == [3, 5]


def test_fallback_followup_text():
    lead = SimpleNamespace(contact_name="Sam Lee", business_name="ABC Bakery")
    root = SimpleNamespace(recipient_name="ABC Bakery")
    first = _fallback_body(1, 2, lead, root, True, "Mahad")
    last = _fallback_body(2, 2, lead, root, False, "Mahad")
    assert first.startswith("Hi Sam,") and first.endswith("Best,")
    assert "leave it here" in last and last.endswith("Best,\nMahad")
    for text in (first, last):
        assert "bump" not in text.lower() and "—" not in text


def test_overnight_window_belongs_to_the_day_it_starts():
    shift = _window(start=17, end=2)
    assert in_send_window(shift, datetime(2026, 10, 9, 23, 0))  # Friday 23:00
    assert in_send_window(shift, datetime(2026, 10, 10, 1, 30))  # Saturday 01:30 = Friday's shift
    assert not in_send_window(shift, datetime(2026, 10, 10, 18, 0))  # Saturday evening
    assert not in_send_window(shift, datetime(2026, 10, 12, 1, 0))  # Monday 01:00 = Sunday's shift
    assert not in_send_window(shift, datetime(2026, 10, 12, 12, 0))  # Monday noon
    assert next_window_open(shift, datetime(2026, 10, 10, 18, 0)) == datetime(2026, 10, 12, 17, 0)


def test_working_day_reset_hour(monkeypatch):
    from app.utils import dates

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 10, 1, 30).astimezone()  # Saturday 01:30

    monkeypatch.setattr(dates, "datetime", FakeDatetime)
    dates.set_day_start_hour(17)
    try:
        assert dates.local_day_start().strftime("%a %H:%M") == "Fri 17:00"
        assert dates.local_today() == date(2026, 10, 9)
    finally:
        dates.set_day_start_hour(0)
    assert dates.local_day_start().strftime("%a %H:%M") == "Sat 00:00"


def test_automatic_sends_are_spaced_out(monkeypatch):
    import pytest

    account = SimpleNamespace(email_address="me@gmail.com")
    monkeypatch.setattr(deliverability, "seconds_since_last_send", lambda db, a: 30)
    assert deliverability.sent_recently(None, account)
    with pytest.raises(SendDeferred) as caught:
        deliverability.require_send_gap(None, account)
    assert caught.value.step == deliverability.WAITING_FOR_SPACING
    monkeypatch.setattr(deliverability, "seconds_since_last_send", lambda db, a: 300)
    deliverability.require_send_gap(None, account)  # long enough: no wait
    monkeypatch.setattr(deliverability, "seconds_since_last_send", lambda db, a: None)
    assert not deliverability.sent_recently(None, account)  # never sent
