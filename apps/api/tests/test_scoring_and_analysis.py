from types import SimpleNamespace

from app.services.analysis import analyze_website_signals
from app.services.scoring import calculate_lead_score, calculate_website_score


def test_website_score_uses_observed_signals_only():
    score, cats = calculate_website_score(
        {
            "reachable": True,
            "https": True,
            "title": True,
            "meta_description": True,
            "has_viewport": True,
            "has_cta": False,
            "has_contact_info": False,
            "has_email_on_site": False,
            "has_nav": True,
            "has_structure": True,
            "word_count": 80,
        }
    )
    assert 0 <= score <= 100
    assert cats["cta"] < 10
    assert cats["technical"] >= 20


def test_analysis_does_not_invent_issues_when_unreachable():
    result = analyze_website_signals(
        text="",
        title="",
        meta_description="",
        signals={"reachable": False, "https": False},
        pages=[],
        platform="unknown",
    )
    codes = {i["code"] for i in result["issues"]}
    assert "unreachable" in codes
    assert "weak_cta" not in codes


def test_lead_score_rewards_email_and_does_not_inflate():
    lead = SimpleNamespace(
        primary_email="info@abc.test",
        website_url="https://abc.test",
        primary_phone="3055551212",
        address="123 Main",
        google_place_id="abc",
        google_maps_url="",
        platform="wix",
        website_score=40,
        do_not_contact=False,
    )
    score, reasons = calculate_lead_score(lead, {"issues": [{"title": "Weak CTA"}]})
    assert 40 <= score <= 90
    assert any("email" in r["reason"].lower() for r in reasons)


def test_do_not_contact_reduces_score():
    lead = SimpleNamespace(
        primary_email="info@abc.test",
        website_url="",
        primary_phone="",
        address="",
        google_place_id="",
        google_maps_url="",
        platform="",
        website_score=None,
        do_not_contact=True,
    )
    score, _ = calculate_lead_score(lead)
    assert score < 20
