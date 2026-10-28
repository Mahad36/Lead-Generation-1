"""Answering a lead's reply isn't outreach: it goes out past the daily limit and doesn't use it up."""
from datetime import UTC, datetime

from app.services import conversations
from app.services.gmail import daily_limit_reached, sent_today_count

from tests.test_send_protection import db, make_account, make_user, pg, sent  # noqa: F401


def test_reply_sends_at_the_daily_limit_and_is_not_counted(db, monkeypatch):
    from app.models import ConversationMessage, EmailMessage

    user = make_user(db)
    account = make_account(db, user, "me@beecreative.pk", limit=3)
    sent(db, account, 3)  # outreach: today's limit used up
    assert daily_limit_reached(db, account)
    root = db.query(EmailMessage).filter(EmailMessage.email_account_id == account.id).first()
    root.rfc_message_id = "<root@x>"
    db.add(ConversationMessage(user_id=user.id, root_message_id=root.id, direction="in", source="inbox",
                               from_email=root.recipient_email, subject="Re: s", body_text="Interested",
                               rfc_message_id="<their@x>", occurred_at=datetime.now(UTC)))
    db.commit()
    monkeypatch.setattr(conversations, "send_gmail_message", lambda db, account, **kw: "gmail-id")
    monkeypatch.setattr(conversations, "tracks_opens", lambda db, user_id: False)
    monkeypatch.setattr(conversations, "account_signature", lambda account: ("", ""))

    conversations.send_reply(db, user.id, root.id, "Thanks, here's a time that works.")

    answer = db.query(ConversationMessage).filter(ConversationMessage.source == "app").one()
    assert db.get(EmailMessage, answer.email_message_id).status == "sent"
    assert sent_today_count(db, account) == 3  # the answer didn't use up outreach capacity
