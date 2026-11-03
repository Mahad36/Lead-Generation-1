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


# ---------------------------------------------------------------- follow-ups and the daily limit


def sent_today(db, account, n, followup_of=None):
    from app.models import EmailMessage

    for i in range(n):
        db.add(EmailMessage(user_id=account.user_id, email_account_id=account.id, recipient_email=f"r{i}@x.com",
                            subject="s", status="sent", sent_at=datetime.now(UTC) - timedelta(minutes=5 + i),
                            followup_of_id=followup_of.id if followup_of else None, followup_step=1 if followup_of else 0))
    db.commit()


def test_follow_ups_dont_use_the_daily_limit_of_a_healthy_inbox(db):
    from app.services.gmail import sent_today_count
    from app.services.send_protection import evaluate

    user = make_user(db)
    account = make_account(db, user, "me@mine.com", limit=3)
    root = started_thread(db, user, account)
    sent_today(db, account, 3, followup_of=root)  # three follow-ups went out today
    assert sent_today_count(db, account) == 0
    assert evaluate(db, account, refresh=False).ok  # first emails: all 3 still available
    sent_today(db, account, 3)  # now the 3 first emails too
    assert evaluate(db, account, refresh=False).stage == "user_limit"
    # follow-ups have their own allowance (as many as the daily limit), so a backlog never becomes a spike
    verdict = evaluate(db, account, refresh=False, followup=True)
    assert not verdict.ok and "follow-ups" in verdict.reason


def test_a_recovering_inbox_counts_every_email(db):
    from app.services.gmail import sent_today_count
    from app.services.send_protection import evaluate

    user = make_user(db)
    account = make_account(db, user, "me@mine.com", limit=12, recommended_limit=3)  # health lowered it to 3
    root = started_thread(db, user, account)
    sent_today(db, account, 3, followup_of=root)
    assert sent_today_count(db, account) == 3
    assert not evaluate(db, account, refresh=False, followup=True).ok
    assert not evaluate(db, account, refresh=False).ok
