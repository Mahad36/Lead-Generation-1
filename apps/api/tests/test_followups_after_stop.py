"""Stopping a campaign / scrape file stops new first emails only: threads already started keep their follow-ups."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services import followups

from tests.test_send_protection import db, make_account, make_user, pg  # noqa: F401


def started_thread(db, user, account, days_ago=4):
    from app.models import EmailMessage

    root = EmailMessage(user_id=user.id, email_account_id=account.id, recipient_email="owner@good.com",
                        subject="Your website", status="sent", sent_at=datetime.now(UTC) - timedelta(days=days_ago),
                        followups_enabled=True)
    db.add(root)
    db.commit()
    return root


def test_stopped_file_or_campaign_keeps_follow_ups_due(db, monkeypatch):
    user = make_user(db)
    root = started_thread(db, user, make_account(db, user, "me@mine.com"))
    for origin in [(None, SimpleNamespace(outreach_status="stopped"), None),
                   (SimpleNamespace(status="stopped", extra={}), None, None)]:
        monkeypatch.setattr(followups, "_origin", lambda db, r, o=origin: o)
        assert [(r.id, step) for r, step, _ in followups.due_followups(db)] == [(root.id, 1)]
        db.refresh(root)
        assert root.followups_done_at is None


def test_campaign_with_follow_ups_switched_off_ends_the_thread(db, monkeypatch):
    user = make_user(db)
    root = started_thread(db, user, make_account(db, user, "me@mine.com"))
    monkeypatch.setattr(followups, "_origin",
                        lambda db, r: (SimpleNamespace(status="running", extra={"followups": False}), None, None))
    assert followups.due_followups(db) == []
    db.refresh(root)
    assert root.followups_done_at is not None
