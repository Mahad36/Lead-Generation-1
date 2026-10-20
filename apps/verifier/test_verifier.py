"""Mailbox checker: how mail server answers are read (the live checks need a server with port 25 open)."""

from verifier import classify


def test_no_such_user_answers():
    assert classify(550, "5.1.1 The email account that you tried to reach does not exist") == "no_such_user"  # Google
    assert classify(550, "5.4.1 Recipient address rejected: Access denied. AS(201806281)") == "no_such_user"  # Microsoft
    assert classify(550, "5.1.10 RESOLVER.ADR.RecipientNotFound; Recipient not found by SMTP address lookup") == "no_such_user"
    assert classify(550, "No such user here") == "no_such_user"


def test_being_blocked_is_never_called_a_dead_mailbox():
    assert classify(550, "5.7.606 Access denied, banned sending IP [1.2.3.4]") == "blocked"
    assert classify(554, "Service unavailable; Client host [1.2.3.4] blocked using zen.spamhaus.org") == "blocked"
    assert classify(550, "5.7.1 Relaying denied") == "blocked"


def test_temporary_and_accepted():
    assert classify(450, "4.2.0 Greylisted, please try again later") == "temporary"
    assert classify(250, "2.1.5 OK") == "accepted"
    assert classify(553, "something unusual") == "unknown"


def test_a_failed_dns_lookup_is_never_invalid(monkeypatch):
    import dns.exception
    import dns.resolver

    import verifier

    def broken(*a, **k):
        raise dns.exception.Timeout()

    monkeypatch.setattr(dns.resolver, "resolve", broken)
    monkeypatch.setattr(verifier, "_doh", lambda name, rtype: None)
    result = verifier.check("info@realbusiness-example.com")
    assert result.status == "unknown" and "DNS" in result.detail


def test_a_domain_that_does_not_exist_is_invalid(monkeypatch):
    import dns.resolver

    import verifier

    def nxdomain(*a, **k):
        raise dns.resolver.NXDOMAIN()

    monkeypatch.setattr(dns.resolver, "resolve", nxdomain)
    assert verifier.check("info@no-such-domain-xyz.com").status == "invalid"
