from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email_account_id: str | None = None
    template_id: str | None = None
    lead_ids: list[str] = []
    import_batch_id: str | None = None
    scraping_job_id: str | None = None
    use_all_ready: bool = False
    use_manual: bool = False
    send_now: bool = False
    subject: str | None = None
    html_body: str | None = None
    text_body: str | None = None
    daily_limit: int | None = None
    hourly_limit: int | None = None
    min_delay_seconds: int | None = None
    max_delay_seconds: int | None = None
    start_date: date | None = None
    scheduled_at: datetime | None = None
    end_date: date | None = None
    require_approval: bool | None = None
    skip_previously_contacted: bool | None = None
    # "audit": analyse each lead's website -> PDF audit -> Claude email written from the findings -> send with PDF.
    # "template": one subject/body for every lead (with an optional AI paragraph), no website analysis.
    outreach_mode: Literal["audit", "template"] = "template"

    @model_validator(mode="after")
    def require_leads_or_import(self):
        if not self.lead_ids and not self.import_batch_id and not self.scraping_job_id and not self.use_all_ready and not self.use_manual:
            raise ValueError("Select a lead file")
        return self


class CampaignUpdate(BaseModel):
    name: str | None = None
    daily_limit: int | None = None
    hourly_limit: int | None = None
    min_delay_seconds: int | None = None
    max_delay_seconds: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    require_approval: bool | None = None


class CampaignLeadOut(BaseModel):
    id: str
    lead_id: str
    status: str
    subject: str
    html_body: str
    text_body: str
    personalized_message: str
    approved: bool
    skip_reason: str
    error_message: str
    sent_at: datetime | None
    business_name: str | None = None
    recipient_email: str | None = None
    contact_name: str = ""
    website_url: str = ""
    city: str = ""
    outreach: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class CampaignOut(BaseModel):
    id: str
    name: str
    status: str
    email_account_id: str | None
    template_id: str | None
    daily_limit: int
    hourly_limit: int
    min_delay_seconds: int
    max_delay_seconds: int
    start_date: date | None
    end_date: date | None
    require_approval: bool
    skip_previously_contacted: bool
    sent_today: int
    last_sent_at: datetime | None
    next_eligible_at: datetime | None
    paused_reason: str
    error_message: str
    created_at: datetime
    lead_count: int = 0
    sent_count: int = 0
    failed_count: int = 0
    pending_count: int = 0
    file_kind: str = ""
    file_id: str = ""
    file_name: str = ""
    scheduled_at: str = ""
    outreach_mode: str = "template"
    auditing_count: int = 0

    model_config = {"from_attributes": True}


class CampaignPreview(BaseModel):
    selected_leads: int
    with_email: int
    suppressed: int
    previously_contacted: int
    do_not_contact: int
    will_send: int
    daily_limit: int
    estimated_duration_minutes: int
    skipped_details: list[dict] = []


class CampaignLeadEdit(BaseModel):
    subject: str | None = None
    html_body: str | None = None
    text_body: str | None = None


class CampaignLeadIds(BaseModel):
    ids: list[str]
