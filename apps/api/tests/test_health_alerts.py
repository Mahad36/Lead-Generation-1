import socket
from types import SimpleNamespace

from app.services import health_alerts
from app.services.health_alerts import blacklist_listings, inbox_alerts


class FakeQuery:
    """Enough of a SQLAlchemy query for inbox_alerts: warm-up counts and the 14-day sent / replied counts."""

    def __init__(self, rows=(), counts=(0, 0)):
        self.rows, self.counts = list(rows), list(counts)

    def filter(self, *a, **k):
        return self

    def group_by(self, *a):
        return self

    def all(self):
        return self.rows

    def count(self):
        return self.counts.pop(0) if self.counts else 0


class FakeDb:
    def __init__(self, warmup=(), sent=0, replied=0):
        self.queries = [FakeQuery(rows=warmup), FakeQuery(counts=(sent, replied))]

    def query(self, *a):
        return self.queries.pop(0)


def account(**extra):
    base = dict(
        id="a1", email_address="mahad@beecreative.pk", health_status="healthy", health_reason="",
        health_signals={"bounces_in_last": [0, 50], "unsubscribes_in_last": [0, 100], "rejected_7d": 0},
        health={}, daily_limit=30,
    )
    return SimpleNamespace(**{**base, **extra})


def kinds(found):
    return {(f["kind"], f["severity"]) for f in found}


def test_healthy_inbox_has_no_alerts():
    found, keys = inbox_alerts(FakeDb(warmup=[("inbox", 20)], sent=80, replied=3), account())
    assert found == [] and "bounces:a1" in keys and "warmup_spam:a1" in keys


def test_each_warning_sign_raises_an_alert():
    acc = account(
        health_status="reduced", health_reason="Sending halved: bounce rate 4% of the last 50 emails.",
        health_signals={"bounces_in_last": [2, 50], "unsubscribes_in_last": [3, 100], "rejected_7d": 1},
        daily_limit=60,
        health={"placement": {"at": "2999-01-01T00:00:00+00:00", "results": [{"to": "x@gmail.com", "landed": "spam"}]}},
    )
    found, _ = inbox_alerts(FakeDb(warmup=[("inbox", 6), ("spam", 4)], sent=70, replied=0), acc)
    assert kinds(found) == {
        ("status", "warning"), ("bounces", "warning"), ("unsubscribes", "warning"), ("blocked", "critical"),
        ("warmup_spam", "critical"), ("placement", "critical"), ("volume", "warning"), ("no_replies", "warning"),
    }


def test_small_samples_dont_alert():
    acc = account(health_signals={"bounces_in_last": [2, 10], "unsubscribes_in_last": [1, 10], "rejected_7d": 0})
    found, _ = inbox_alerts(FakeDb(warmup=[("spam", 2)], sent=10, replied=0), acc)
    assert found == []


def test_blacklists(monkeypatch):
    answers = {"bad.com.dbl.spamhaus.org": "127.0.1.2", "blocked.com.multi.surbl.org": "127.0.0.1"}

    def lookup(name):
        if name in answers:
            return answers[name]
        raise socket.gaierror("NXDOMAIN")

    monkeypatch.setattr(health_alerts.socket, "gethostbyname", lookup)
    assert blacklist_listings("bad.com") == ["Spamhaus DBL"]
    assert blacklist_listings("good.com") == []
    assert blacklist_listings("blocked.com") == []  # 127.0.0.1 = the list refused the query: not a listing

    def offline(name):
        raise OSError("no network")

    monkeypatch.setattr(health_alerts.socket, "gethostbyname", offline)
    assert blacklist_listings("good.com") is None  # unknown, not "clean"


def test_one_bounce_in_a_young_inbox_is_not_a_warning():
    from app.services.account_health import decide

    acc = SimpleNamespace(status="connected", health_status="reduced", paused_reason="", restricted_until=None,
                          ramp_started_on=None, daily_limit=10, health_reason="")
    one = {"bounces_in_last": [1, 20], "bounce_rate": 0.05, "unsubscribes_in_last": [0, 20], "sent_total": 20}
    assert decide(acc, one)[0] != "reduced"
    two = {**one, "bounces_in_last": [2, 20], "bounce_rate": 0.10}
    status, reason, factor = decide(acc, two)
    assert status == "reduced" and reason.startswith("Warning signs") and factor == 0.5
    found, _ = inbox_alerts(FakeDb(warmup=[("inbox", 5)], sent=20, replied=1),
                            account(health_status="healthy", health_signals={"bounces_in_last": [1, 20]}))
    assert "bounces" not in {f["kind"] for f in found}


def test_one_unsubscribe_doesnt_hold_an_inbox():
    from app.services.account_health import decide

    acc = SimpleNamespace(status="connected", health_status="healthy", paused_reason="", restricted_until=None,
                          ramp_started_on=None, daily_limit=8, health_reason="")
    one = {"bounces_in_last": [1, 50], "bounce_rate": 0.02, "unsubscribes_in_last": [1, 50],
           "unsubscribe_rate": 0.02, "sent_total": 50}
    assert decide(acc, one)[0] != "reduced"
    found, _ = inbox_alerts(FakeDb(warmup=[("inbox", 5)], sent=50, replied=1),
                            account(health_signals={"bounces_in_last": [1, 50], "unsubscribes_in_last": [1, 50]}))
    assert "unsubscribes" not in {f["kind"] for f in found}
    # 2-4%: a warning you can review, never a hold by itself
    few = {**one, "unsubscribes_in_last": [2, 50], "unsubscribe_rate": 0.04}
    assert decide(acc, few)[0] == "monitoring"
    found, _ = inbox_alerts(FakeDb(warmup=[("inbox", 5)], sent=50, replied=1),
                            account(health_signals={"bounces_in_last": [1, 50], "unsubscribes_in_last": [2, 50]}))
    assert ("unsubscribes", "warning") in kinds(found)
    # 5%+: held as before
    many = {**one, "unsubscribes_in_last": [3, 50], "unsubscribe_rate": 0.06}
    assert decide(acc, many)[0] == "reduced"
    found, _ = inbox_alerts(FakeDb(warmup=[("inbox", 5)], sent=50, replied=1),
                            account(health_signals={"bounces_in_last": [1, 50], "unsubscribes_in_last": [3, 50]}))
    assert ("unsubscribes", "critical") in kinds(found)
