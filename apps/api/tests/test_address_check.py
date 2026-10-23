"""The local risk check of recipient addresses (services/address_check.py) and its enforcement before every first
email and follow-up. DNS is faked throughout and every send is mocked: nothing touches the network or sends mail."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services import address_check, deliverability
from app.services.address_check import BLOCKED, DNS_UNAVAILABLE, HELD, LOW_RISK, RETRY_LATER, run_checks

from test_send_protection import db, make_account, make_user, pg  # noqa: F401 (fixtures: real PostgreSQL)


def resolver(zones: dict):
    """A fake DNS: zones maps (name, type) to an answer; None = the resolvers didn't answer; missing = no records."""

    def query(name, rtype):
        key = (name, rtype)
        if key in zones:
            return zones[key]
        return {"Status": 0, "Answer": []}

    return query


def mx(*hosts, status=0):
    return {"Status": status, "Answer": [{"type": 15, "data": h} for h in hosts]}


A = {"Status": 0, "Answer": [{"type": 1, "data": "192.0.2.1"}]}

ZONES = {
    ("good.com", "MX"): mx("10 mx.good.com."),
    ("mx.good.com", "A"): A,
    ("gone.com", "MX"): {"Status": 3},  # NXDOMAIN
    ("broken.com", "MX"): {"Status": 2},  # SERVFAIL
    ("nullmx.com", "MX"): mx("0 ."),
    ("nomx.com", "MX"): mx(),
    ("parked.com", "MX"): mx("10 mx.sedoparking.com."),
    ("deadmx.com", "MX"): mx("10 mx.old-host.com."),
    ("down.com", "MX"): None,
    ("halfdown.com", "MX"): mx("10 mx.halfdown.com."),
    ("mx.halfdown.com", "A"): None,
    ("mx.halfdown.com", "AAAA"): None,
}


@pytest.fixture
def dns(monkeypatch):
    zones = dict(ZONES)
    monkeypatch.setattr(deliverability, "dns_query", resolver(zones))
    return zones


# ---------------------------------------------------------------- the checks


@pytest.mark.parametrize(
    "email, status, kind",
    [
        ("owner@good.com", LOW_RISK, ""),
        ("not an email", BLOCKED, "format"),
        ("owner@@good.com", BLOCKED, "format"),
        ("john..smith@good.com", BLOCKED, "format"),
        ("owner@good", BLOCKED, "format"),
        ("x" * 65 + "@good.com", BLOCKED, "format"),
        ("user@domain.com", BLOCKED, "not_business"),  # template placeholder
        ("someone@mailinator.com", BLOCKED, "disposable"),
        ("owner@gone.com", BLOCKED, "no_domain"),
        ("owner@nullmx.com", BLOCKED, "null_mx"),
        ("owner@parked.com", BLOCKED, "parked"),
        ("owner@deadmx.com", BLOCKED, "dead_mx"),
        ("owner@broken.com", HELD, "dns_error"),
        ("owner@nomx.com", HELD, "no_mx"),
        ("owner@down.com", HELD, DNS_UNAVAILABLE),
        ("owner@halfdown.com", HELD, DNS_UNAVAILABLE),
    ],
)
def test_each_check(dns, email, status, kind):
    result = run_checks(email)
    assert (result.status, result.kind) == (status, kind)
    assert result.reason or status == LOW_RISK
    assert result.checks and result.checks[-1]["outcome"] == ("pass" if status == LOW_RISK else
                                                              "fail" if status == BLOCKED else "unknown")


def test_uncertain_results_hold_and_only_dns_outages_retry(dns):
    down, nomx = run_checks("owner@down.com"), run_checks("owner@nomx.com")
    assert down.retry_later and down.reason.startswith(RETRY_LATER)
    assert not nomx.retry_later and nomx.status == HELD  # an inconclusive answer about the domain itself


def test_never_called_mailbox_verification(dns):
    """DNS and format checks can't confirm a mailbox: nothing is labelled verified / valid / deliverable."""
    for email in ("owner@good.com", "owner@nomx.com", "owner@gone.com"):
        result = run_checks(email).as_dict()
        words = " ".join([result["status"], result["kind"], result["reason"]]
                         + [f"{c['check']} {c['outcome']} {c['detail']}" for c in result["checks"]]).lower()
        assert "verified" not in words and "valid" not in words.replace("invalid", "") and "deliverable" not in words
        assert "not mailbox verification" in result["label"]
    assert {LOW_RISK, BLOCKED, HELD} == {"low_risk", "blocked", "held"}


def test_no_outside_services_or_mail_servers_are_contacted(dns, monkeypatch):
    import smtplib
    import socket
    import urllib.request

    def forbidden(*a, **k):
        raise AssertionError("the local risk check must not open connections")

    monkeypatch.setattr(smtplib, "SMTP", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    assert run_checks("owner@good.com").status == LOW_RISK


# ---------------------------------------------------------------- stored results, staleness, history


def test_results_are_stored_and_reused_while_fresh(db, dns):
    user = make_user(db)
    first = address_check.check(db, user.id, "Owner@Good.com")
    assert first.low_risk and address_check.latest(db, user.id, "owner@good.com")["status"] == LOW_RISK
    dns[("good.com", "MX")] = None  # DNS goes down: the fresh result still stands
    assert address_check.check(db, user.id, "owner@good.com").low_risk


def test_a_stale_pass_is_checked_again_and_fails_closed(db, dns):
    user = make_user(db)
    old = datetime.now(UTC) - address_check.FRESH_FOR - timedelta(minutes=1)
    assert address_check.check(db, user.id, "owner@good.com", now=old).low_risk
    dns[("good.com", "MX")] = None  # can't be re-checked now
    with pytest.raises(address_check.AddressHeld) as held:
        address_check.enforce(db, user.id, "owner@good.com")
    assert held.value.result.retry_later


def test_a_dns_outage_is_never_remembered(db, dns):
    user = make_user(db)
    dns[("good.com", "MX")] = None
    assert address_check.check(db, user.id, "owner@good.com").status == HELD
    dns[("good.com", "MX")] = ZONES[("good.com", "MX")]  # back up: checked again at once, not after a day
    assert address_check.check(db, user.id, "owner@good.com").low_risk


def test_an_address_that_bounced_is_blocked_even_with_a_fresh_pass(db, dns):
    from app.models import EmailMessage

    user = make_user(db)
    assert address_check.check(db, user.id, "owner@good.com").low_risk
    db.add(EmailMessage(user_id=user.id, recipient_email="OWNER@good.com", subject="s", status="sent",
                        sent_at=datetime.now(UTC), bounced_at=datetime.now(UTC)))
    db.commit()
    result = address_check.check(db, user.id, "owner@good.com")
    assert result.status == BLOCKED and result.kind == "bounced_before"


def test_address_problem_reports_only_what_rules_an_address_out(db, dns):
    user = make_user(db)
    assert "doesn't exist" in deliverability.address_problem(db, user.id, "owner@gone.com")
    assert deliverability.address_problem(db, user.id, "owner@nomx.com") == ""  # a hold, enforced at sending
    assert deliverability.address_problem(db, user.id, "owner@good.com") == ""


# ---------------------------------------------------------------- enforced before the first email


def ready_audit(db, user, email, **lead_extra):
    from app.models import Lead, WebsiteAudit
    from app.services.website_audit import PDF_VERSION

    lead = Lead(user_id=user.id, business_name="Acme", primary_email=email, website_url="https://good.com", **lead_extra)
    db.add(lead)
    db.flush()
    audit = WebsiteAudit(user_id=user.id, lead_id=lead.id, website_url="https://good.com", status="ready",
                         email_subject="Your website", email_html="<p>Hi there,</p><p>Notes.</p><p>Best,</p>",
                         recipient_email=email, pdf_data=b"%PDF-1.4", site_data={"pdf_version": PDF_VERSION},
                         auto_send=True)
    db.add(audit)
    db.commit()
    return audit


@pytest.fixture
def mailer(monkeypatch):
    """Every send is recorded instead of sent."""
    from app.services import website_audit

    sends = []
    monkeypatch.setattr(website_audit, "send_gmail_message",
                        lambda db, account, **k: sends.append({"to": k["to"], "cc": k.get("cc")}) or "id")
    monkeypatch.setattr(website_audit, "account_signature", lambda account: ("", ""))
    monkeypatch.setattr(deliverability, "require_send_time", lambda *a, **k: None)
    return sends


@pytest.mark.parametrize("email", ["owner@nomx.com", "owner@broken.com", "owner@gone.com", "owner@parked.com"])
def test_audit_email_is_not_sent_when_the_check_fails_or_is_inconclusive(db, dns, mailer, email):
    from fastapi import HTTPException

    from app.models import WebsiteAudit
    from app.services.website_audit import send_audit

    user = make_user(db)
    account = make_account(db, user, "me@mine.com")
    audit = ready_audit(db, user, email)
    with pytest.raises(HTTPException):
        send_audit(db, user.id, audit.id, email_account_id=account.id)
    assert mailer == []
    audit = db.get(WebsiteAudit, audit.id)
    db.refresh(audit)
    assert audit.status == "ready" and not audit.auto_send and audit.error_message
    held = address_check.latest(db, user.id, email)["status"] == HELD
    assert audit.step.startswith("Held:") if held else audit.step.startswith("Not sent:")


def test_audit_email_waits_while_dns_is_down_and_goes_once_it_answers(db, dns, mailer):
    from app.services.deliverability import SendDeferred
    from app.services.website_audit import send_audit

    user = make_user(db)
    account = make_account(db, user, "me@mine.com")
    audit = ready_audit(db, user, "owner@good.com")
    dns[("good.com", "MX")] = None
    with pytest.raises(SendDeferred) as waiting:
        send_audit(db, user.id, audit.id, email_account_id=account.id)
    assert waiting.value.step.startswith("Waiting: " + RETRY_LATER) and mailer == []
    dns[("good.com", "MX")] = ZONES[("good.com", "MX")]
    sent = send_audit(db, user.id, audit.id, email_account_id=account.id)
    assert sent.status == "sent" and mailer == [{"to": "owner@good.com", "cc": []}]


def test_manual_send_is_held_too(db, dns, mailer):
    from fastapi import HTTPException

    from app.services.website_audit import send_audit

    user = make_user(db)
    account = make_account(db, user, "me@mine.com")
    audit = ready_audit(db, user, "owner@good.com")
    dns[("good.com", "MX")] = None
    with pytest.raises(HTTPException) as held:
        send_audit(db, user.id, audit.id, email_account_id=account.id, manual=True)
    assert held.value.status_code == 400 and mailer == []


def test_cc_addresses_that_dont_pass_are_left_out(db, dns, mailer, monkeypatch):
    from app.services import website_audit

    user = make_user(db)
    account = make_account(db, user, "me@mine.com")
    audit = ready_audit(db, user, "owner@good.com")
    monkeypatch.setattr(website_audit, "select_recipients", lambda *a, **k: {
        "to": "owner@good.com", "cc": ["sales@good.com", "office@nomx.com", "team@gone.com"], "reason": ""})
    from app.models import Lead, LeadEmail

    lead = db.get(Lead, audit.lead_id)
    db.add(LeadEmail(lead_id=lead.id, email="owner@good.com", is_primary=True))
    db.commit()
    db.refresh(lead)
    website_audit.send_audit(db, user.id, audit.id, email_account_id=account.id)
    assert mailer == [{"to": "owner@good.com", "cc": ["sales@good.com"]}]


def test_audit_scheduler_holds_leads_and_pauses_while_dns_is_down(db, dns, monkeypatch):
    from app.models import Lead
    from app.services import auto_audits

    user = make_user(db)
    held = Lead(user_id=user.id, business_name="A", primary_email="owner@nomx.com", website_url="https://a.com")
    db.add(held)
    db.commit()
    assert auto_audits.checked_recipient(db, user.id, held) == address_check.latest(db, user.id, "owner@nomx.com")["reason"]
    assert auto_audits.held_or_undeliverable(db, user.id, held) == "held"
    dns[("good.com", "MX")] = None
    waiting = Lead(user_id=user.id, business_name="B", primary_email="owner@good.com", website_url="https://b.com")
    db.add(waiting)
    db.commit()
    assert auto_audits.checked_recipient(db, user.id, waiting).startswith(RETRY_LATER)


# ---------------------------------------------------------------- enforced before every follow-up


def thread(db, user, account, to):
    from app.models import EmailMessage

    root = EmailMessage(user_id=user.id, email_account_id=account.id, recipient_email=to, subject="Your website",
                        status="sent", sent_at=datetime.now(UTC) - timedelta(days=4), followups_enabled=True)
    db.add(root)
    db.commit()
    return root


@pytest.fixture
def followup_mailer(monkeypatch):
    from app.services import followups

    sends, written = [], []
    monkeypatch.setattr(followups, "send_gmail_message", lambda db, account, **k: sends.append(k["to"]) or "id")
    monkeypatch.setattr(followups, "account_signature", lambda account: ("", ""))
    monkeypatch.setattr(followups, "_write_body",
                        lambda *a, **k: written.append(1) or "Hi there,\n\nJust checking in on the audit.\n\nBest,")
    return sends, written


def test_followup_to_a_failed_address_is_not_sent_and_the_thread_ends(db, dns, followup_mailer):
    from app.services import followups

    sends, written = followup_mailer
    user = make_user(db)
    account = make_account(db, user, "me@mine.com")
    root = thread(db, user, account, "owner@gone.com")
    with pytest.raises(address_check.AddressHeld):
        followups.send_followup(db, root, 1, 2)
    db.refresh(root)
    assert sends == [] and written == [] and root.followups_done_at  # no Claude tokens spent on it either


def test_followup_waits_while_inconclusive_and_sends_once_clear(db, dns, followup_mailer, monkeypatch):
    from app.services import followups

    sends, _ = followup_mailer
    user = make_user(db)
    account = make_account(db, user, "me@mine.com")
    root = thread(db, user, account, "owner@good.com")
    dns[("good.com", "MX")] = None
    monkeypatch.setattr(followups, "due_followups", lambda db: [(root, 1, 2)])
    monkeypatch.setattr(followups, "_on_hold", lambda db, root: False)
    monkeypatch.setattr(followups, "_send_time_ok", lambda db, root: True)
    assert followups.run_followups(db) == {"sent": 0, "waiting": 1}
    db.refresh(root)
    assert sends == [] and root.followups_done_at is None  # held, not ended: checked again next round
    dns[("good.com", "MX")] = ZONES[("good.com", "MX")]
    assert followups.run_followups(db)["sent"] == 1 and sends == ["owner@good.com"]


def test_followup_is_held_for_an_address_that_bounced_since(db, dns, followup_mailer):
    from app.models import EmailMessage
    from app.services import followups

    sends, _ = followup_mailer
    user = make_user(db)
    account = make_account(db, user, "me@mine.com")
    root = thread(db, user, account, "owner@good.com")
    assert address_check.check(db, user.id, "owner@good.com").low_risk
    db.add(EmailMessage(user_id=user.id, recipient_email="owner@good.com", subject="other", status="sent",
                        sent_at=datetime.now(UTC), bounced_at=datetime.now(UTC)))
    db.commit()
    with pytest.raises(address_check.AddressHeld):
        followups.send_followup(db, root, 1, 2)
    assert sends == []
