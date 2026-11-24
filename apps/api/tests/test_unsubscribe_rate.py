"""The unsubscribe rate behind account health counts only people who used the unsubscribe link, once each.
A reply asking to stop and your own "Do not contact" still block the address but don't hold the inbox.
Real PostgreSQL, nothing is sent."""

from datetime import UTC, datetime, timedelta

from app.services.account_health import gather_signals

from test_send_protection import db, make_account, make_user, pg, sent  # noqa: F401 (fixtures: real PostgreSQL)


def _email(db, account, to, minutes_ago):
    from app.models import EmailMessage

    db.add(EmailMessage(user_id=account.user_id, email_account_id=account.id, recipient_email=to, subject="s",
                        status="sent", sent_at=datetime.now(UTC) - timedelta(minutes=minutes_ago)))


def _suppress(db, user, email, reason, source):
    from app.models import SuppressionEntry

    db.add(SuppressionEntry(user_id=user.id, email=email, reason=reason, source=source))


def test_only_unsubscribe_link_counts_once_per_person(db):
    user = make_user(db)
    account = make_account(db, user, "a@own.test")
    sent(db, account, 96)
    # Do not contact set by you, after a first email and a follow-up: not counted
    _email(db, account, "brooke@x.com", 60)
    _email(db, account, "brooke@x.com", 30)
    _suppress(db, user, "brooke@x.com", "manual", "do_not_contact")
    # A reply asking to stop: not counted
    _email(db, account, "replied@x.com", 20)
    _suppress(db, user, "replied@x.com", "replied_opt_out", "inbox_check")
    # Unsubscribe link, two emails to the same person: counted once
    _email(db, account, "Ken@X.com", 15)
    db.commit()
    signals = gather_signals(db, account)
    assert signals["unsubscribes_in_last"] == [0, 100] and signals["unsubscribe_rate"] == 0.0

    _suppress(db, user, "ken@x.com", "user_unsubscribed", "unsubscribe_link")
    _email(db, account, "ken@x.com", 10)
    db.commit()
    signals = gather_signals(db, account)
    assert signals["unsubscribes_in_last"][0] == 1
