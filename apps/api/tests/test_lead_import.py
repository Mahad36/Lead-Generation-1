from app.schemas.campaigns import CampaignCreate
from app.services.leads import HEADER_ALIASES, outreach_status
from pydantic import ValidationError


def test_csv_headers_accept_common_aliases():
    assert HEADER_ALIASES["name"] == "business_name"
    assert HEADER_ALIASES["website"] == "website_url"
    assert HEADER_ALIASES["email"] == "primary_email"


def test_campaign_accepts_imported_file_without_lead_ids():
    payload = CampaignCreate(
        name="CSV outreach",
        email_account_id="11111111-1111-1111-1111-111111111111",
        template_id="22222222-2222-2222-2222-222222222222",
        import_batch_id="33333333-3333-3333-3333-333333333333",
    )
    assert payload.import_batch_id
    assert payload.lead_ids == []


def test_campaign_requires_leads_or_import():
    try:
        CampaignCreate(
            name="Empty",
            email_account_id="11111111-1111-1111-1111-111111111111",
            template_id="22222222-2222-2222-2222-222222222222",
        )
    except ValidationError as exc:
        assert "lead file" in str(exc)
        return
    raise AssertionError("expected validation error")


def test_campaign_accepts_written_email():
    payload = CampaignCreate(
        name="CSV outreach",
        import_batch_id="33333333-3333-3333-3333-333333333333",
        subject="Hello {{business_name}}",
        html_body="Hi {{business_name}}, let's talk.",
    )
    assert payload.subject
    assert payload.html_body


def test_campaign_accepts_scrape_file():
    payload = CampaignCreate(
        name="Miami plumbers",
        scraping_job_id="44444444-4444-4444-4444-444444444444",
        send_now=True,
    )
    assert payload.scraping_job_id
    assert payload.send_now


def test_campaign_accepts_all_ready_leads_and_send_now():
    payload = CampaignCreate(
        name="All ready",
        use_all_ready=True,
        send_now=True,
    )
    assert payload.use_all_ready
    assert payload.send_now
    assert payload.email_account_id is None
    assert payload.template_id is None


def test_scrape_job_defaults_to_auto_send():
    from app.schemas.scraping import ScrapingJobCreate, ScrapingSearchIn

    payload = ScrapingJobCreate(
        searches=[ScrapingSearchIn(country="United States", city="Miami", category="Plumbers")]
    )
    assert payload.auto_send is True


def test_leads_without_email_are_not_ready():
    assert outreach_status("info@shop.example") == "ready"
    assert outreach_status("") == "no_email"
    assert outreach_status("   ") == "no_email"
