"""A fresh start: earlier bounces and unsubscribes stop counting for an inbox, while new ones count straight away."""
from datetime import UTC, datetime, timedelta

from tests.test_send_protection import db, make_account, make_user, pg  # noqa: F401


def mail(db, account, n, bounced=0, days_ago=1):
    from app.models import EmailMessage

    when = datetime.now(UTC) - timedelta(days=days_ago)
    for i in range(n):
        db.add(EmailMessage(user_id=account.user_id, email_account_id=account.id, recipient_email=f"r{i}@x{days_ago}.com",
                            subject="s", status="sent", sent_at=when - timedelta(minutes=i),
                            bounced_at=when if i < bounced else None))
    db.commit()


def held_inbox(db):
    from app.services.account_health import assess

    user = make_user(db)
    account = make_account(db, user, "me@mine.com", limit=18)
    mail(db, account, 47, bounced=3)  # 6%: critical, held, at half capacity
    assess(db, account)
    assert account.health_status == "reduced" and account.recommended_limit == 9
    return user, account


def test_fresh_start_clears_the_warnings_and_returns_to_the_daily_limit(db):
    from app.services.account_health import start_fresh
    from app.services.deliverability import effective_daily_limit
    from app.services.send_protection import evaluate, low_speed

    _, account = held_inbox(db)
    assert not evaluate(db, account).ok
    account.health = {**account.health, "low_speed": {"since": datetime.now(UTC).isoformat(), "per_day": 3}}
    db.commit()
    start_fresh(db, account)
    assert account.health_status == "healthy" and account.recommended_limit == 18
    assert low_speed(db, account) is None and effective_daily_limit(account) == 18
    verdict = evaluate(db, account)
    assert verdict.ok and verdict.safe_capacity == 18


def test_new_bounces_raise_the_warnings_again(db):
    from app.services.account_health import assess, start_fresh
    from app.services.deliverability import check_bounce_rate
    from app.services.send_protection import evaluate

    _, account = held_inbox(db)
    start_fresh(db, account)
    assert check_bounce_rate(db, account) is False  # the old bounces don't pause it
    mail(db, account, 20, bounced=2, days_ago=-1)  # sent after the fresh start: 10%
    assess(db, account)
    assert account.health_status == "reduced" and not evaluate(db, account).ok


def test_gmail_trouble_still_holds_after_a_fresh_start(db):
    from app.services.account_health import start_fresh
    from app.services.send_protection import evaluate

    _, account = held_inbox(db)
    account.restricted_until = datetime.now(UTC) + timedelta(hours=2)
    db.commit()
    start_fresh(db, account)
    assert account.health_status == "paused" and not evaluate(db, account).ok


def test_the_overview_keeps_the_real_rate_and_the_warning_starts_over(db):
    from app.services.account_health import start_fresh
    from app.services.health_alerts import domain_bounce_stats

    _, account = held_inbox(db)
    start_fresh(db, account)
    assert domain_bounce_stats(db, [account]) == {"sent": 47, "bounced": 3, "rate": 3 / 47}
    assert domain_bounce_stats(db, [account], fresh=True)["sent"] == 0
