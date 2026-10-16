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
