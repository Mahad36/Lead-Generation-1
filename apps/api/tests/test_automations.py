"""Automation offers: chosen from what the website shows (evidence) and the business type (fit)."""

from datetime import UTC, datetime

from app.services.audit_ai import normalize_report
from app.services.audit_pdf import build_audit_pdf
from app.services.automations import candidates, industry_order, merge_signals, parse_offers, website_signals
from app.services.website_audit import keep_offered_automations

PHONE_ONLY = '<a href="tel:+13055550100">Call us</a><p>Book an appointment today</p>'
QUOTE_FORM = '<form><input type="email" name="email"></form><h2>Get a free quote</h2>'


def test_signals_from_the_pages():
    s = website_signals([PHONE_ONLY])
    assert s["phone_link"] and s["booking_cta"] and not s["booking_tool"] and not s["chat_widget"]
    s = website_signals(['<script src="https://assets.calendly.com/widget.js"></script>' + QUOTE_FORM])
    assert s["booking_tool"] == "calendly" and s["form"] and s["quote_form"]
    assert website_signals([""], [{"name": "Tawk.to chat", "category": "chat"}])["chat_widget"] == "chat"


def test_dentist_with_phone_only_booking_gets_online_booking_first():
    found = candidates(website_signals([PHONE_ONLY]), "Cosmetic dentist")
    assert found[0]["key"] == "online_booking" and found[0]["basis"] == "seen"
    assert "couldn't find an online booking tool" in found[0]["evidence"]


def test_roofer_with_a_quote_form_gets_instant_reply_and_quote_followup():
    found = candidates(website_signals([QUOTE_FORM]), "Roofing contractor")
    assert [f["key"] for f in found][:2] == ["instant_reply", "quote_followup"]


def test_same_gap_different_fix_per_business():
    assert industry_order("Dentist")[0] == "online_booking"
    assert industry_order("Med spa")[0] == "ai_assistant"
    assert industry_order("Personal injury lawyer")[0] == "instant_reply"
    assert industry_order("Something else")[0] == "instant_reply"


def test_only_automations_you_offer():
    found = candidates(website_signals([PHONE_ONLY]), "Dentist", offered=["review_requests"])
    assert [f["key"] for f in found] == ["review_requests"]
    assert candidates(website_signals([PHONE_ONLY]), "Dentist", offered=[]) == []
    assert parse_offers("online_booking, nonsense ,ai_assistant") == ["online_booking", "ai_assistant"]
    assert parse_offers(None)[0] == "online_booking" and parse_offers("") == []


def test_nothing_seen_becomes_a_question():
    everything = {"booking_tool": "calendly", "chat_widget": "tawk.to", "review_widget": "birdeye", "form": True,
                  "quote_form": False, "phone_link": True, "booking_cta": True}
    found = candidates(everything, "Dentist")
    assert len(found) == 1 and found[0]["basis"] == "question" and found[0]["key"] == "online_booking"


def test_browser_signals_are_merged():
    merged = merge_signals({"chat_widget": "", "form": True}, {"chat_widget": "intercom", "form": False})
    assert merged == {"chat_widget": "intercom", "form": True}


def test_analysis_can_only_keep_offered_candidates():
    options = candidates(website_signals([PHONE_ONLY]), "Dentist", offered=["online_booking", "review_requests"])
    report = normalize_report({
        "executive_summary": "Summary.", "headline": "Headline",
        "findings": [{"title": "Slow homepage", "severity": "high", "category": "Speed", "evidence": "e",
                      "impact": "i", "recommendation": "r", "status": "improvement"}],
        "automation_opportunities": [
        {"key": "ai_assistant", "title": "Chat", "evidence": "x", "recommendation": "y", "business_relevance": "z"},
        {"key": "online_booking", "title": "Book online", "evidence": "x", "recommendation": "y", "business_relevance": "z"},
    ]})
    kept = keep_offered_automations(report["automation_opportunities"], options)
    assert [k["key"] for k in kept] == ["online_booking"] and kept[0]["basis"] == "seen"


def test_pdf_has_the_automation_page():
    report = {"overall_score": 60, "findings": [], "automation_opportunities": [
        {"key": "online_booking", "title": "Let patients book online", "basis": "seen",
         "evidence": "Booking is by phone only.", "recommendation": "Online booking with reminders.",
         "business_relevance": "New patients can book after hours."}]}
    pdf = build_audit_pdf(report=report, performance={"url": "https://x.com"}, lead={"business_name": "X Dental"},
                          sender={"company_name": "Bee Creative"}, generated_at=datetime.now(UTC))
    assert pdf.startswith(b"%PDF")


def test_ordinary_words_are_not_mistaken_for_tools():
    text = "<p>We respond in a timely manner, top of the podium, broadly speaking, on Ocean Boulevard. We weave quality in.</p>"
    s = website_signals([text])
    assert not s["booking_tool"] and not s["chat_widget"] and not s["review_widget"]
    assert website_signals(['<script src="https://app.gettimely.com/book.js"></script>'])["booking_tool"] == "gettimely.com"


def test_website_vendor_and_marketplace_addresses_are_not_leads():
    from app.utils.emails import is_plausible_email

    for email in ("support@agentfire.com", "service@atom.com", "info@prosites.com", "help@mail.hubspot.com", "filler@godaddy.com"):
        assert not is_plausible_email(email), email
    for email in ("aburke@onesothebysrealty.com", "info@borgesroofing.com", "owner@gmail.com", "dpo@warren-james.co.uk"):
        assert is_plausible_email(email), email


def test_website_vendor_and_placeholder_addresses_are_never_leads():
    from app.utils.emails import is_plausible_email

    for junk in ("support@agentfire.com", "service@atom.com", "filler@godaddy.com", "info@prosites.com",
                 "mymail@mailservice.com", "help@mail.hubspot.com"):
        assert not is_plausible_email(junk), junk
    for real in ("aburke@onesothebysrealty.com", "info@borgesroofing.com", "owner@gmail.com", "info@dh-dental.co.uk"):
        assert is_plausible_email(real), real


def test_free_offers_follow_the_outreach_focus():
    from types import SimpleNamespace

    from app.services.website_audit import AUTOMATION_FREE_OFFERS, DEFAULT_FREE_OFFERS, free_offers

    assert free_offers(SimpleNamespace(free_offers="", outreach_focus="automation"))[0] == AUTOMATION_FREE_OFFERS[0]
    assert free_offers(SimpleNamespace(free_offers="", outreach_focus="website")) == DEFAULT_FREE_OFFERS
    assert free_offers(SimpleNamespace(free_offers="My own offer", outreach_focus="automation")) == ["My own offer"]


def test_focus_reaches_the_analysis_prompt():
    from app.services.audit_ai import build_audit_context

    assert '"outreach_focus": "automation"' in build_audit_context(lead={}, site={}, performance={}, outreach_focus="automation")


def test_pdf_puts_automations_first_when_automation_leads():
    from datetime import UTC, datetime

    report = {"overall_score": 60, "findings": [{"title": "Slow homepage", "severity": "high", "evidence": "e",
                                                "impact": "i", "recommendation": "r"}],
              "automation_opportunities": [{"key": "instant_reply", "title": "Reply to every enquiry within a minute",
                                            "basis": "seen", "evidence": "e", "recommendation": "r", "business_relevance": "b"}]}
    pdfs = {}
    for first in (True, False):
        pdfs[first] = build_audit_pdf(report=report, performance={"url": "https://x.com"}, lead={"business_name": "X"},
                                      sender={"company_name": "Bee Creative"}, generated_at=datetime.now(UTC),
                                      automation_first=first)
    assert pdfs[True].startswith(b"%PDF") and pdfs[False].startswith(b"%PDF")


def test_email_rules_ask_for_one_strong_automation_line():
    from app.services.audit_ai import EMAIL_SYSTEM

    assert "ONE strong line" in EMAIL_SYSTEM and "single sentence" in EMAIL_SYSTEM
    assert '"AI" is fine' in EMAIL_SYSTEM and "never a list" in EMAIL_SYSTEM


def test_address_choice_prefers_info_and_the_leads_own_branch():
    from types import SimpleNamespace

    from app.services.contacts import ranked_contacts

    def row(email):
        return SimpleNamespace(email=email, verification_status="domain_ok", email_type="", contact_name="", job_title="",
                               verification_details={})

    lead = SimpleNamespace(city="Miami, Florida", address="", emails=[row("admin@miamibeachds.com"), row("info@miamibeachds.com")])
    assert ranked_contacts(lead)[0][0].email == "info@miamibeachds.com"  # was admin@ (alphabetical tie)
    sher = SimpleNamespace(city="Miami, Florida", address="12000 Biscayne Blvd #130, North Miami, FL 33181",
                           emails=[row(e) for e in ("adventura@sherdental.com", "aventura@sherdental.com",
                                                    "northmiami@sherdental.com", "fortlauderdale@sherdental.com")])
    best = ranked_contacts(sher)[0]
    assert best[0].email == "northmiami@sherdental.com" and "North Miami" in best[2]


def test_zerobounce_role_based_is_not_a_dead_mailbox(monkeypatch):
    from app.services import email_verification as ev

    class Response:
        status_code, content = 200, b"x"

        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    for sub, expected in (("role_based", "valid"), ("role_based_catch_all", "accept_all"), ("disposable", "invalid")):
        monkeypatch.setattr(ev.httpx, "get", lambda *a, **k: Response({"status": "do_not_mail", "sub_status": sub}))
        assert ev.verify_with("zerobounce", "k", "info@x.com")["status"] == expected, sub
    assert ev.risky_reason("admin@acme.com", "https://acme.com", "Dentist", {})  # admin@ is always checked


def test_admin_inboxes_and_likely_typos_are_skipped():
    from types import SimpleNamespace

    from app.services.contacts import ranked_contacts

    def row(email, status="domain_ok", provider=None):
        return SimpleNamespace(email=email, verification_status=status, email_type="", contact_name="", job_title="",
                               verification_details={"provider": provider} if provider else {})

    lead = SimpleNamespace(city="", address="", emails=[row("admin@acme.com"), row("webmaster@acme.com")])
    assert ranked_contacts(lead) == []  # never the website admin's inbox
    lead.emails.append(row("admin@acme.com".replace("admin", "info")))
    assert [r[0].email for r in ranked_contacts(lead)] == ["info@acme.com"]
    confirmed = SimpleNamespace(city="", address="", emails=[row("admin@acme.com", "valid", "zerobounce")])
    assert ranked_contacts(confirmed)[0][0].email == "admin@acme.com"  # unless verified as working
    twins = SimpleNamespace(city="Miami", address="", emails=[row("adventura@sher.com"), row("aventura@sher.com"),
                                                              row("info@sher.com")])
    assert [r[0].email for r in ranked_contacts(twins)] == ["info@sher.com"]


def test_a_rejected_address_falls_back_to_the_next_one(monkeypatch):
    from types import SimpleNamespace

    from app.services import auto_audits

    rows = [SimpleNamespace(email=e, verification_status="domain_ok", email_type="", contact_name="", job_title="",
                            verification_details={}, is_primary=False, rank_reason="")
            for e in ("info@dead.com", "office@dead.com")]
    lead = SimpleNamespace(id="l1", city="", address="", emails=rows, primary_email="info@dead.com", extra={})
    db = SimpleNamespace(commit=lambda: None)
    monkeypatch.setattr(auto_audits, "address_problem", lambda db, user, email: "")
    monkeypatch.setattr(auto_audits, "verification_problem",
                        lambda db, user, lead, email: "ZeroBounce says the mailbox doesn't exist" if email == "info@dead.com" else "")
    assert auto_audits.checked_recipient(db, "u", lead) == ""
    assert lead.primary_email == "office@dead.com" and rows[0].verification_status == "invalid"


def test_admin_only_lead_needs_a_confirmed_mailbox(monkeypatch):
    from types import SimpleNamespace

    from app.services import auto_audits

    row = SimpleNamespace(email="admin@acme.com", verification_status="domain_ok", email_type="", contact_name="",
                          job_title="", verification_details={}, is_primary=True, rank_reason="")
    lead = SimpleNamespace(id="l1", city="", address="", emails=[row], primary_email="admin@acme.com", extra={})
    db = SimpleNamespace(commit=lambda: None)
    monkeypatch.setattr(auto_audits, "address_problem", lambda *a: "")
    monkeypatch.setattr(auto_audits, "verification_problem", lambda *a: "")  # verification off: nothing confirmed
    assert "admin" in auto_audits.checked_recipient(db, "u", lead)

    def confirm(db, user, lead, email):
        row.verification_status, row.verification_details = "valid", {"provider": "zerobounce"}
        return ""

    monkeypatch.setattr(auto_audits, "verification_problem", confirm)
    assert auto_audits.checked_recipient(db, "u", lead) == ""
