"""Agency outreach: agency files get a white-label pitch Claude writes from the agency's own site and your offer notes,
with your company profile PDF attached, through the same sending pipeline as audit emails.
Real PostgreSQL, mocked crawl / Claude / Gmail: nothing is fetched or sent."""

import json
from types import SimpleNamespace

import pytest

from app.services import website_audit

from test_address_check import dns  # noqa: F401 (fake DNS fixture)
from test_send_protection import db, make_account, make_user, pg  # noqa: F401 (fixtures: real PostgreSQL)

PROFILE = b"%PDF-1.4 company profile"
SITE = {
    "url": "https://acme-agency.com",
    "title": "Acme | Shopify Plus agency in Austin",
    "meta_description": "We build and grow Shopify stores for DTC brands.",
    "business_summary": "Shopify Plus agency for DTC fashion brands",
    "services": ["Shopify store builds", "CRO"],
    "platform": "WordPress",
    "pages_crawled": [{"url": "https://acme-agency.com", "title": "Home"}],
    "text_excerpt": "Acme builds Shopify stores for fashion brands.",
}


class FakeClaude:
    def __init__(self, reply: dict):
        self.reply, self.calls = reply, []

    def generate(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return json.dumps(self.reply)


def setup_agency(db, user, *, profile=PROFILE, notes="White-label Shopify development and CRO. 48-hour turnaround."):
    from app.models import UserSettings

    settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    settings.agency_profile_pdf, settings.agency_profile_filename = profile, "Bee Creative - Company Profile.pdf"
    settings.agency_pitch, settings.company_name, settings.sender_name = notes, "Bee Creative", "Ahad"
    db.commit()


def agency_audit(db, user, email="owner@good.com"):
    from app.models import Lead, WebsiteAudit

    lead = Lead(user_id=user.id, business_name="Acme Agency", primary_email=email, website_url="https://good.com",
                city="Austin", category="Shopify agency")
    db.add(lead)
    db.flush()
    audit = WebsiteAudit(user_id=user.id, lead_id=lead.id, website_url="https://good.com", status="queued",
                         kind="agency", trigger="auto", recipient_email=email)
    db.add(audit)
    db.commit()
    return audit


@pytest.fixture
def pipeline(monkeypatch):
    """Crawl, Claude and Gmail mocked; the speed test and screenshots must never run for an agency pitch."""
    claude = FakeClaude({
        "subject": "white-label Shopify builds for Acme",
        "body_html": "<p>Hi there,</p><p>Saw you build Shopify Plus stores for DTC fashion brands.</p><p>Best,</p>",
        "reference_sites": ["https://shop-one.com", "https://made-up.com"],
    })
    sends = []

    def forbidden(*a, **k):
        raise AssertionError("an agency pitch runs no speed test or screenshots")

    monkeypatch.setattr(website_audit, "_collect_site_data", lambda lead, url: dict(SITE))
    monkeypatch.setattr(website_audit, "_discover_contacts", lambda *a, **k: None)
    monkeypatch.setattr(website_audit, "measure_performance", forbidden)
    monkeypatch.setattr(website_audit, "capture_screenshots", forbidden)
    monkeypatch.setattr(website_audit, "get_ai_provider_for_user", lambda db, user_id: claude)
    monkeypatch.setattr(website_audit, "resolve_ai_config",
                        lambda db, user_id: SimpleNamespace(provider="claude_code", model="opus"))
    monkeypatch.setattr(website_audit, "portfolio_for_prompt",
                        lambda db, user_id: {"Shopify": ["https://shop-one.com (Shopify)"]})
    monkeypatch.setattr(website_audit, "send_gmail_message",
                        lambda db, account, **k: sends.append(k) or "id")
    monkeypatch.setattr(website_audit, "account_signature", lambda account: ("", ""))
    from app.services import deliverability

    monkeypatch.setattr(deliverability, "require_send_time", lambda *a, **k: None)
    return SimpleNamespace(claude=claude, sends=sends)


def test_agency_pitch_is_written_from_their_site_and_sends_your_profile(db, dns, pipeline):
    from app.models import UserSettings, WebsiteAudit

    user = make_user(db)
    account = make_account(db, user, "me@mine.com")
    setup_agency(db, user)
    audit = agency_audit(db, user)

    assert website_audit.run_audit(db, audit.id) == "ready"
    audit = db.get(WebsiteAudit, audit.id)
    assert audit.email_subject == "white-label Shopify builds for Acme"
    # Only portfolio sites from your sheet are kept as links
    assert [s["url"] for s in audit.report["reference_sites"]] == ["https://shop-one.com"]
    assert audit.pdf_data is None and audit.pdf_size == len(PROFILE)
    call = pipeline.claude.calls[0]
    assert "white-label" in call["system"]
    sent_to_claude = json.loads(call["prompt"].split("\n\n", 1)[1])
    # An introduction with all your services: nothing from the agency's site, and no person's name (any inbox may
    # send it; its own signature carries the name)
    assert sent_to_claude["agency"] == {"name": "Acme Agency", "greeting_name": ""}
    assert SITE["business_summary"] not in call["prompt"] and "Ahad" not in call["prompt"]
    assert "48-hour turnaround" in sent_to_claude["offer"]["notes"]

    # The latest profile you uploaded is what goes out
    settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    settings.agency_profile_pdf = b"%PDF-1.4 updated profile"
    db.commit()
    sent = website_audit.send_audit(db, user.id, audit.id, email_account_id=account.id)
    assert sent.status == "sent"
    attachment = pipeline.sends[0]["attachments"][0]
    assert attachment.content == b"%PDF-1.4 updated profile"
    assert attachment.filename == "Bee Creative - Company Profile.pdf"
    assert website_audit.get_pdf(db, user.id, audit.id)[0] == b"%PDF-1.4 updated profile"


@pytest.mark.parametrize("missing", ["profile", "notes"])
def test_pitch_needs_your_profile_and_offer_notes(db, pipeline, missing):
    from app.models import WebsiteAudit
    from app.services.auto_audits import outreach_issues

    user = make_user(db)
    make_account(db, user, "me@mine.com")
    setup_agency(db, user, **({"profile": None} if missing == "profile" else {"notes": ""}))
    issues = outreach_issues(db, user.id, True, "agency")
    assert any(("company profile" if missing == "profile" else "What you offer agencies") in i for i in issues)
    assert not any("company profile" in i for i in outreach_issues(db, user.id, True, "audit"))

    audit = agency_audit(db, user)
    assert website_audit.run_audit(db, audit.id) == "failed"
    audit = db.get(WebsiteAudit, audit.id)
    assert "Agency outreach" in audit.error_message and pipeline.claude.calls == []


def test_agency_and_audit_emails_are_listed_apart(db):
    from app.models import Lead, WebsiteAudit

    user = make_user(db)
    lead = Lead(user_id=user.id, business_name="X", primary_email="x@x.com")
    db.add(lead)
    db.flush()
    for kind in ("audit", "agency", "agency"):
        db.add(WebsiteAudit(user_id=user.id, lead_id=lead.id, website_url=f"https://{kind}.com", kind=kind,
                            status="ready", step="Ready to review and send"))
    db.commit()
    assert website_audit.count_audits(db, user.id, kind="audit") == 1
    assert website_audit.count_audits(db, user.id, kind="agency", view="main") == 2
    assert website_audit.count_audits(db, user.id) == 3


def test_agency_followups_use_the_partnership_wording(db, monkeypatch):
    from datetime import UTC, datetime

    from app.models import EmailMessage, Lead, WebsiteAudit
    from app.services import followups

    user = make_user(db)
    account = make_account(db, user, "me@mine.com")
    lead = Lead(user_id=user.id, business_name="Acme Agency", primary_email="owner@good.com")
    db.add(lead)
    db.flush()
    root = EmailMessage(user_id=user.id, email_account_id=account.id, lead_id=lead.id, recipient_email="owner@good.com",
                        subject="white-label", text_body="first email", status="sent", sent_at=datetime.now(UTC))
    db.add(root)
    db.flush()
    db.add(WebsiteAudit(user_id=user.id, lead_id=lead.id, website_url="https://good.com", kind="agency",
                        status="sent", email_message_id=root.id, report={"kind": "agency"}))
    db.commit()
    claude = FakeClaude({"body": "Hi there,\n\n" + "One small project first could show how we work together. " * 3
                         + "\n\nBest,"})
    import app.services.ai as ai

    monkeypatch.setattr(ai, "get_ai_provider_for_user", lambda db, user_id: claude)
    body = followups._write_body(db, root, 1, 2, lead, None, True)
    assert "small project" in body
    assert "white-label delivery partner" in claude.calls[0]["system"]
    assert "audit_highlights" not in claude.calls[0]["prompt"]

    monkeypatch.setattr(ai, "get_ai_provider_for_user", lambda db, user_id: (_ for _ in ()).throw(RuntimeError()))
    fallback = followups._write_body(db, root, 2, 2, lead, None, True)
    assert "Acme Agency" in fallback and "audit" not in fallback.lower()


def test_agency_emails_tabs_sent_waiting_not_sent(db):
    from datetime import UTC, datetime

    from app.models import Lead, WebsiteAudit

    user = make_user(db)
    lead = Lead(user_id=user.id, business_name="X", primary_email="x@x.com")
    db.add(lead)
    db.flush()
    rows = {
        "sent": dict(status="sent", sent_at=datetime.now(UTC)),
        "ready": dict(status="ready", step="Ready to review and send"),
        "writing": dict(status="drafting_email"),
        "failed": dict(status="failed"),
        "held": dict(status="ready", step="Not sent: the address may bounce (choose another on the lead)"),
        "stopped": dict(status="cancelled"),
    }
    for name, fields in rows.items():
        db.add(WebsiteAudit(user_id=user.id, lead_id=lead.id, website_url=f"https://{name}.com", kind="agency", **fields))
    db.add(WebsiteAudit(user_id=user.id, lead_id=lead.id, website_url="https://audit.com", kind="audit", status="sent",
                        sent_at=datetime.now(UTC)))
    db.commit()

    def sites(view):
        found = website_audit.list_audits(db, user.id, view=view, kind="agency", limit=50)
        return {a.website_url.removeprefix("https://").removesuffix(".com") for a in found}

    assert sites("sent") == {"sent"}
    assert sites("pending") == {"ready", "writing"}
    assert sites("not_sent") == {"failed", "held"}
