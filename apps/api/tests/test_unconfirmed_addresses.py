"""Only addresses the verification service confirms are emailed: role_based (hello@, info@), catch-all and unknown
results are blocked, and a confirmed address of the same business is used instead."""
from datetime import UTC, datetime
from types import SimpleNamespace

from tests.test_send_protection import (  # noqa: F401
    campaign_with_lead,
    db,
    lead_for,
    make_account,
    make_user,
    no_network,
    pg,
    sent,
    verifier,
)


def _month():
    return datetime.now(UTC).strftime("%Y-%m")


def checks_used_up(db, user):
    from app.models import UserSettings

    settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).one()
    settings.email_verifier_monthly_limit, settings.email_verifier_usage = 1, {"month": _month(), "used": 1}
    db.commit()
    return settings


def test_only_confirmed_addresses_are_ccd(db, verifier):
    from app.models import LeadEmail
    from app.services.email_verification import confirmed_cc

    user, calls, _ = verifier
    lead = lead_for(db, user, "owner@shop.com", "https://shop.com")
    for address, status, details in [("sales@shop.com", "valid", {"provider": "zerobounce"}),
                                     ("tom@shop.com", "unknown", {"provider": "zerobounce"}),
                                     ("amy@shop.com", "domain_ok", {})]:
        db.add(LeadEmail(lead_id=lead.id, email=address, verification_status=status, verification_details=details))
    db.commit()
    db.refresh(lead)
    cc = ["Sales@shop.com", "tom@shop.com", "amy@shop.com", "elsewhere@shop.com"]
    assert confirmed_cc(db, user.id, lead, cc) == cc  # only "risky" leads are checked: nothing to go by
    scope_all(db, user)
    assert confirmed_cc(db, user.id, lead, cc) == ["Sales@shop.com"]
    assert confirmed_cc(db, user.id, lead, []) == [] and calls == []  # no check is spent on a CC


def test_used_up_checks_hold_new_addresses_and_resume(db, verifier):
    from app.services.address_check import RETRY_LATER
    from app.services.email_verification import verification_problem

    user, calls, answer = verifier
    scope_all(db, user)
    settings = checks_used_up(db, user)
    lead = lead_for(db, user, "nohosalonyc@gmail.com", "https://nohosalon.com")  # went out unchecked, and bounced
    problem = verification_problem(db, user.id, lead, lead.primary_email)
    assert problem.startswith(RETRY_LATER) and "limit of 1" in problem and calls == []
    settings.email_verifier_monthly_limit = 5  # more checks allowed: checked and sent, without any other step
    db.commit()
    answer["status"] = "valid"
    assert verification_problem(db, user.id, lead, lead.primary_email) == "" and len(calls) == 1
    settings.email_verifier_monthly_limit = 0  # already confirmed: needs no new check, so it still goes out
    db.commit()
    assert verification_problem(db, user.id, lead, lead.primary_email) == ""


def test_no_credits_or_no_answer_hold_and_credits_coming_back_resume(db, verifier):
    from app.models import UserSettings
    from app.services.address_check import RETRY_LATER
    from app.services.email_verification import VerifierError, verification_problem

    user, _, answer = verifier
    scope_all(db, user)
    settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).one()
    lead = lead_for(db, user, "owner@shop.com", "https://shop.com")
    answer["raise"] = ConnectionError("no network")
    assert verification_problem(db, user.id, lead, lead.primary_email).startswith(RETRY_LATER)
    answer["raise"] = VerifierError("ZeroBounce: out of credits", quota=True)
    assert verification_problem(db, user.id, lead, lead.primary_email).startswith(RETRY_LATER)
    db.refresh(settings)
    assert settings.email_verifier_usage["exhausted"] is True
    answer.pop("raise")  # credits added: the next round checks again
    answer["status"] = "valid"
    assert verification_problem(db, user.id, lead, lead.primary_email) == ""
    db.refresh(settings)
    assert settings.email_verifier_usage["exhausted"] is False


def test_a_held_lead_starts_no_audit_and_isnt_given_up_on(db, verifier, monkeypatch):
    from app.models import Lead
    from app.services import auto_audits

    user, _, _ = verifier
    scope_all(db, user)
    checks_used_up(db, user)
    lead = lead_for(db, user, "owner@shop.com", "https://shop.com")
    monkeypatch.setattr(auto_audits, "address_problem", lambda *a, **k: "")
    monkeypatch.setattr(auto_audits, "address_check", lambda *a, **k: SimpleNamespace(low_risk=True, reason=""))
    assert auto_audits.checked_recipient(db, user.id, lead).startswith(auto_audits.RETRY_LATER)
    lead = db.get(Lead, lead.id)
    assert lead.primary_email == "owner@shop.com" and lead.email_status != "undeliverable"


def scope_all(db, user):
    from app.models import UserSettings

    settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).one()
    settings.email_verifier_scope, settings.email_verifier_monthly_limit = "all", 50
    db.commit()


def test_role_based_and_catch_all_results_are_blocked(db, verifier):
    from app.services.email_verification import verification_problem

    user, calls, answer = verifier
    scope_all(db, user)
    for status in ("unknown", "accept_all"):
        lead = lead_for(db, user, f"hello@{status}.com", f"https://{status}.com")
        answer["status"] = status
        problem = verification_problem(db, user.id, lead, lead.primary_email)
        assert problem.startswith("Blocked:") and "couldn't confirm" in problem and "domain" not in problem
        assert verification_problem(db, user.id, lead, lead.primary_email).startswith("Blocked:")  # stored result
    answer["status"] = "valid"
    lead = lead_for(db, user, "hello@confirmed.com", "https://confirmed.com")
    assert verification_problem(db, user.id, lead, lead.primary_email) == ""
    assert len(calls) == 3  # each address checked once


def test_a_confirmed_address_ranks_above_an_unconfirmed_shared_inbox():
    from app.services.contacts import ranked_contacts

    def row(email, status, detail):
        return SimpleNamespace(email=email, verification_status=status, email_type="generic", contact_name="",
                               job_title="", verification_details={"provider": "zerobounce", "detail": detail})

    lead = SimpleNamespace(website_url="https://www.nezevents.com/", city="", address="", business_name="Nez Events",
                           emails=[row("hello@nezevents.com", "unknown", "do_not_mail / role_based"),
                                   row("f.santana14@gmail.com", "valid", "valid")])
    ranked = ranked_contacts(lead)
    assert ranked[0][0].email == "f.santana14@gmail.com"
    assert "not confirmed" in ranked[-1][2]


def test_confirmed_and_likely_addresses_are_tried_before_shared_inboxes(db, verifier):
    from app.services.auto_audits import candidate_leads_query

    user, _, _ = verifier
    shared = lead_for(db, user, "info@a.com", "https://a.com")
    department = lead_for(db, user, "sales.team@b.com", "https://b.com")
    named = lead_for(db, user, "sarah@c.com", "https://c.com")
    free = lead_for(db, user, "shop@gmail.com", "https://d.com")
    confirmed = lead_for(db, user, "info@e.com", "https://e.com")
    confirmed.email_verification_status = "valid"
    db.commit()
    order = [lead.id for lead in candidate_leads_query(db, user.id).all()]
    assert order == [confirmed.id, free.id, named.id, shared.id, department.id]


def file_with_leads(db, user, monkeypatch, count):
    """A running file; "starting" an audit only records it, as an email waiting for its slot."""
    from app.models import Lead, ScrapingJob, WebsiteAudit
    from app.services import auto_audits

    def start(db, user_id, lead_id, **kwargs):
        db.add(WebsiteAudit(user_id=user_id, lead_id=lead_id, website_url="https://x.com", auto_send=True, status="ready"))
        db.commit()

    monkeypatch.setattr(auto_audits, "start_audit", start)
    monkeypatch.setattr(auto_audits, "outreach_issues", lambda *a, **k: [])
    monkeypatch.setattr(auto_audits, "address_problem", lambda *a, **k: "")
    monkeypatch.setattr(auto_audits, "address_check", lambda *a, **k: SimpleNamespace(low_risk=True, reason=""))
    job = ScrapingJob(user_id=user.id, name="f", status="completed", outreach_status="running",
                      outreach_auto_send=True, extra={})
    db.add(job)
    db.flush()
    for i in range(count):
        db.add(Lead(user_id=user.id, business_name=f"B{i}", primary_email=f"sarah{i}@b{i}.com",
                    website_url=f"https://b{i}.com", scraping_job_id=job.id, status="ready"))
    db.commit()
    return job


def poll_until_quiet(db, job):
    from app.services import auto_audits

    started = 0
    for _ in range(10):
        started += auto_audits.poll_job(db, job)
    return started


def test_no_check_is_spent_when_todays_limit_is_already_reached(db, verifier, monkeypatch):
    user, calls, answer = verifier
    scope_all(db, user)
    answer["status"] = "valid"
    account = make_account(db, user, "a@gmail.com", limit=3)
    sent(db, account, 3)
    job = file_with_leads(db, user, monkeypatch, 5)
    assert poll_until_quiet(db, job) == 0 and calls == []


def test_checks_stop_once_todays_limit_has_its_emails(db, verifier, monkeypatch):
    user, calls, answer = verifier
    scope_all(db, user)
    answer["status"] = "valid"
    make_account(db, user, "a@gmail.com", limit=3)
    job = file_with_leads(db, user, monkeypatch, 8)
    assert poll_until_quiet(db, job) == 3 and len(calls) == 3  # 5 leads left unchecked, for another day


def test_a_failed_check_doesnt_use_up_a_slot_of_todays_limit(db, verifier, monkeypatch):
    from app.services import auto_audits

    user, calls, answer = verifier
    scope_all(db, user)
    make_account(db, user, "a@gmail.com", limit=2)
    job = file_with_leads(db, user, monkeypatch, 8)
    answer["status"] = "invalid"
    assert auto_audits.poll_job(db, job) == 0
    failed = len(calls)
    assert failed >= 1
    answer["status"] = "valid"
    assert poll_until_quiet(db, job) == 2 and len(calls) == failed + 2


def test_confirmed_leads_fill_the_limit_before_any_check_is_spent(db, verifier, monkeypatch):
    from app.models import Lead, LeadEmail

    user, calls, answer = verifier
    scope_all(db, user)
    answer["status"] = "valid"
    make_account(db, user, "a@gmail.com", limit=2)
    job = file_with_leads(db, user, monkeypatch, 4)
    for lead in db.query(Lead).filter(Lead.scraping_job_id == job.id).order_by(Lead.primary_email).limit(2).all():
        lead.email_verification_status = "valid"
        db.add(LeadEmail(lead_id=lead.id, email=lead.primary_email, is_primary=True, verification_status="valid",
                         verification_details={"provider": "zerobounce"}))
    db.commit()
    assert poll_until_quiet(db, job) == 2 and calls == []


def test_a_campaign_spends_no_check_when_its_inbox_is_full(db, verifier, no_network):
    from app.models import CampaignLead
    from app.services.campaigns import send_claimed_lead

    user, calls, answer = verifier
    scope_all(db, user)
    answer["status"] = "valid"
    account = make_account(db, user, "a@gmail.com", limit=1)
    sent(db, account, 1)
    _, campaign_lead = campaign_with_lead(db, user, account)
    assert send_claimed_lead(db, campaign_lead) == "daily_limit"
    assert calls == [] and no_network == []
    assert db.get(CampaignLead, campaign_lead.id).status == "queued"  # waits for tomorrow; not given up on
    account.daily_limit = 5
    campaign_lead.status = "sending"
    db.commit()
    assert send_claimed_lead(db, campaign_lead) == "sent"
    assert len(calls) == 1 and no_network == ["a@gmail.com"]
