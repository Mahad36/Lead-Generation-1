import smtplib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services import followups
from app.services.account_health import (
    backoff_minutes,
    classify_send_error,
    decide,
    rank_accounts,
)
from app.services.auto_audits import retry_wait_minutes
from app.services.deliverability import account_ready, effective_daily_limit

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def acct(name, *, status="connected", health="healthy", until=None, paused="", default=False, limit=50, recommended=None):
    return SimpleNamespace(
        id=name,
        email_address=f"{name}@gmail.com",
        status=status,
        health_status=health,
        health_reason="",
        restricted_until=until,
        paused_reason=paused,
        is_default=default,
        daily_limit=limit,
        ramp_started_on=None,
        recommended_limit=recommended,
    )


GOOD = {
    "sent_total": 200,
    "sent_24h_app": 20,
    "bounce_rate": 0.0,
    "bounces_in_last": [0, 50],
    "unsubscribe_rate": 0.0,
    "unsubscribes_in_last": [0, 100],
    "failures_24h": {},
    "rejected_7d": 0,
    "provider_limit": 500,
}


# ---------------------------------------------------------------- Gmail error kinds


def test_gmail_errors_are_classified():
    assert classify_send_error(smtplib.SMTPDataError(421, b"4.4.5 Server busy, try again later."))[0] == "temporary"
    assert classify_send_error(smtplib.SMTPDataError(421, b"4.7.28 Our system has detected an unusual rate"))[0] == "rate_limited"
    assert classify_send_error(smtplib.SMTPDataError(550, b"5.4.5 Daily user sending limit exceeded."))[0] == "quota"
    assert classify_send_error(smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted"))[0] == "auth"
    assert classify_send_error(smtplib.SMTPRecipientsRefused({"x@y.com": (550, b"5.1.1 user unknown")}))[0] == "recipient"
    assert classify_send_error(smtplib.SMTPDataError(550, b"5.1.1 The email account that you tried to reach does not exist"))[0] == "recipient"
    assert classify_send_error(smtplib.SMTPDataError(552, b"5.7.0 This message was blocked because its content"))[0] == "rejected"
    assert classify_send_error(smtplib.SMTPServerDisconnected("Connection unexpectedly closed"))[0] == "temporary"


def test_backoff_grows_and_is_capped():
    assert [backoff_minutes(n) for n in (1, 2, 3, 4)] == [2, 4, 8, 16]
    assert backoff_minutes(10) == 60
    assert backoff_minutes(1, "rate_limited") == 8  # rate limits wait longer
    assert [retry_wait_minutes(n) for n in (1, 2, 3)] == [10, 20, 40]
    assert retry_wait_minutes(20) == 360


# ---------------------------------------------------------------- status and capacity


def test_status_from_evidence():
    assert decide(acct("a"), GOOD, NOW)[0] == "healthy"
    assert decide(acct("a"), {**GOOD, "sent_total": 5}, NOW)[0] == "monitoring"  # not enough history
    status, reason, factor = decide(acct("a"), {**GOOD, "bounce_rate": 0.04, "bounces_in_last": [2, 50]}, NOW)
    assert (status, factor) == ("reduced", 0.5) and "bounce" in reason
    assert decide(acct("a"), {**GOOD, "failures_24h": {"temporary": 3}}, NOW)[0] == "reduced"
    assert decide(acct("a"), {**GOOD, "rejected_7d": 1}, NOW)[0] == "reduced"
    # close to Gmail's own daily limit: paused until it rolls over
    assert decide(acct("a"), {**GOOD, "sent_24h_gmail": 420}, NOW)[0] == "paused"


def test_restrictions_pause_and_reconnect():
    assert decide(acct("a", until=NOW + timedelta(hours=24)), GOOD, NOW)[0] == "paused"  # quota hit
    assert decide(acct("a", until=NOW - timedelta(minutes=1)), GOOD, NOW)[0] == "healthy"  # cleared
    assert decide(acct("a", paused="Too many bounces"), GOOD, NOW)[0] == "paused"
    assert decide(acct("a", status="expired"), GOOD, NOW)[0] == "reconnect"
    assert decide(acct("a", health="reconnect"), GOOD, NOW)[0] == "reconnect"


def test_health_lowers_the_daily_limit():
    assert effective_daily_limit(acct("a", limit=40, recommended=20)) == 20
    assert effective_daily_limit(acct("a", limit=40, recommended=None)) == 40
    assert effective_daily_limit(acct("a", limit=40, recommended=20), use_recommended=False) == 40


# ---------------------------------------------------------------- rotation


def test_unequal_capacities_and_eligibility():
    big, small = acct("big"), acct("small", default=True)
    assert rank_accounts([(small, 3), (big, 30)]) == [big, small]  # more room, more emails
    restricted = acct("r", until=datetime.now(UTC) + timedelta(minutes=10))
    gone = acct("d", status="disconnected")
    reconnect = acct("x", health="reconnect")
    full = acct("f")
    ranked = rank_accounts([(restricted, 50), (gone, 50), (reconnect, 50), (full, 0), (small, 3)])
    assert ranked == [small]  # restricted, disconnected, needing reconnection and full accounts get nothing
    assert rank_accounts([(full, 0)]) == []  # nobody can send: the queue waits


def test_health_based_prefers_healthier_accounts():
    healthy = acct("h", health="healthy")
    reduced = acct("r", health="reduced")
    monitoring = acct("m", health="monitoring")
    assert rank_accounts([(reduced, 40), (monitoring, 5), (healthy, 10)], "health") == [healthy, monitoring, reduced]
    assert rank_accounts([(reduced, 40), (monitoring, 5), (healthy, 10)], "auto")[0] == reduced  # auto: most room


def test_account_ready():
    assert account_ready(acct("a"))
    assert not account_ready(acct("a", until=datetime.now(UTC) + timedelta(minutes=5)))
    assert not account_ready(acct("a", health="reconnect"))
    assert not account_ready(acct("a", paused="x"))
    assert not account_ready(None)


# ---------------------------------------------------------------- duplicates


class _Query:
    def __init__(self, result):
        self.result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self.result


def test_a_follow_up_step_is_never_sent_twice():
    already = SimpleNamespace(id="m2", status="sent")
    db = SimpleNamespace(query=lambda *a, **k: _Query(already))
    root = SimpleNamespace(id="root")
    # The step already went out (e.g. a retry after a restart): it is returned, not sent again
    assert followups.send_followup(db, root, 1, 2) is already
