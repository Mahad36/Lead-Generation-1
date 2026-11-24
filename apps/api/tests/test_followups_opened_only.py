"""Follow-ups go only to leads who opened an email of the thread (Settings, on by default)."""
from datetime import UTC, datetime, timedelta

from app.services import followups

from tests.test_send_protection import db, make_account, make_user, pg  # noqa: F401


def thread(db, user, days_ago, opens=0):
    from app.models import EmailMessage

    root = EmailMessage(user_id=user.id, email_account_id=make_account(db, user, f"me{days_ago}{opens}@mine.com").id,
                        recipient_email="owner@good.com", subject="Your website", status="sent",
                        sent_at=datetime.now(UTC) - timedelta(days=days_ago), followups_enabled=True, open_count=opens)
    db.add(root)
    db.commit()
    return root


def settings_of(db, user, **values):
    from app.models import UserSettings

    settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).one()
    for key, value in values.items():
        setattr(settings, key, value)
    db.commit()
    return settings


def due_ids(db):
    return [r.id for r, _, _ in followups.due_followups(db)]


def test_unopened_email_waits_and_opened_email_is_followed_up(db, monkeypatch):
    monkeypatch.setattr(followups, "_origin", lambda db, r: (None, None, None))
    user = make_user(db)
    assert settings_of(db, user).followups_opened_only is True  # on by default
    unopened, opened = thread(db, user, 4), thread(db, user, 4, opens=1)
    assert due_ids(db) == [opened.id]
    db.refresh(unopened)
    assert unopened.followups_done_at is None  # still waiting: an open later sends it
    assert followups.followup_summary(db, unopened)["waiting_for_open"] is True
    unopened.open_count = 1
    db.commit()
    assert set(due_ids(db)) == {opened.id, unopened.id}


def test_never_opened_thread_ends_after_the_schedule(db, monkeypatch):
    monkeypatch.setattr(followups, "_origin", lambda db, r: (None, None, None))
    user = make_user(db)
    old = thread(db, user, 9)  # "3,5": 8 days without an open
    assert due_ids(db) == []
    db.refresh(old)
    assert old.followups_done_at is not None


def test_switched_off_or_without_open_tracking_everyone_is_followed_up(db, monkeypatch):
    monkeypatch.setattr(followups, "_origin", lambda db, r: (None, None, None))
    user = make_user(db)
    root = thread(db, user, 4)
    settings_of(db, user, followups_opened_only=False)
    assert due_ids(db) == [root.id]
    settings_of(db, user, followups_opened_only=True, track_opens=False)
    assert due_ids(db) == [root.id]
