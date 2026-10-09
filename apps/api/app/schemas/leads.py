from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LeadEmailOut(BaseModel):
    id: str
    email: str
    is_primary: bool
    source_url: str
    verification_status: str

    model_config = {"from_attributes": True}


class LeadPhoneOut(BaseModel):
    id: str
    phone: str
    is_primary: bool
    source: str

    model_config = {"from_attributes": True}


class LeadCreate(BaseModel):
    business_name: str = Field(min_length=1, max_length=500)
    contact_name: str = Field(default="", max_length=255)
    category: str = ""
    country: str = ""
    city: str = ""
    address: str = ""
    primary_phone: str = ""
    primary_email: str = ""
    website_url: str = ""
    google_maps_url: str = ""
    google_place_id: str = ""
    rating: float | None = None
    review_count: int | None = None
    notes: str = ""


class LeadUpdate(BaseModel):
    business_name: str | None = None
    contact_name: str | None = Field(default=None, max_length=255)
    category: str | None = None
    country: str | None = None
    city: str | None = None
    address: str | None = None
    primary_phone: str | None = None
    primary_email: str | None = None
    website_url: str | None = None
    notes: str | None = None
    do_not_contact: bool | None = None


class LeadListItem(BaseModel):
    id: str
    business_name: str
    contact_name: str = ""
    category: str
    city: str
    country: str
    primary_phone: str
    primary_email: str
    website_url: str
    platform: str
    website_score: int | None
    lead_score: int | None
    email_status: str
    email_verification_status: str
    scraped_at: datetime | None
    do_not_contact: bool
    status: str = "new"
    source: str = "google_maps"
    import_batch_id: str | None = None
    scraping_job_id: str | None = None
    file_kind: str = ""
    file_id: str = ""
    file_name: str = ""
    # Audit -> PDF -> email -> tracking status (services/lead_outreach.py)
    outreach: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class LeadFileOut(BaseModel):
    id: str
    kind: str
    name: str
    status: str
    lead_count: int
    ready_leads: int
    detail: str = ""
    created_at: datetime | None = None


class LeadDetail(LeadListItem):
    address: str
    google_maps_url: str
    google_place_id: str
    rating: float | None
    review_count: int | None
    website_domain: str
    lead_score_reasons: list[Any]
    notes: str
    created_at: datetime
    last_contacted_at: datetime | None
    emails: list[LeadEmailOut] = []
    phones: list[LeadPhoneOut] = []
    website: dict[str, Any] | None = None


class LeadListResponse(BaseModel):
    items: list[LeadListItem]
    total: int
    page: int
    page_size: int


class LeadIdsRequest(BaseModel):
    ids: list[str]


class ImportSummary(BaseModel):
    import_id: str
    filename: str
    status: str
    imported: int
    duplicates: int
    invalid: int
    new: int
    queued_for_analysis: int = 0
    errors: list[str] = []


class LeadImportOut(BaseModel):
    id: str
    filename: str
    status: str
    lead_count: int
    ready_leads: int
    failed_leads: int
    analyzed_leads: int
    duplicate_leads: int
    invalid_rows: int
    error_message: str
    created_at: datetime

    model_config = {"from_attributes": True}
