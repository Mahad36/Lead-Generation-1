from datetime import datetime

from typing import Any, Literal

from pydantic import BaseModel, Field


class ScrapingSearchIn(BaseModel):
    country: str = Field(min_length=1, max_length=128)
    city: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=255)


class ScrapingJobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    searches: list[ScrapingSearchIn] = Field(min_length=1)
    max_results_per_search: int | None = Field(default=None, ge=1)
    auto_send: bool = True
    subject: str | None = None
    html_body: str | None = None
    # Automatic outreach for this file: "auto" (audit + send), "review" (audit, I send), "off"
    outreach_mode: Literal["auto", "review", "off"] = "off"
    outreach_daily_limit: int = Field(default=10, ge=1, le=500)


class OutreachAction(BaseModel):
    action: Literal["start", "pause", "resume", "stop", "update"]
    auto_send: bool | None = None
    daily_limit: int | None = Field(default=None, ge=1, le=500)


class ScrapingSearchOut(BaseModel):
    id: str
    country: str
    city: str
    category: str
    query: str
    status: str
    sort_order: int
    businesses_found: int
    new_leads: int
    duplicate_leads: int
    current_page: int
    error_message: str

    model_config = {"from_attributes": True}


class ScrapingJobOut(BaseModel):
    id: str
    name: str
    status: str
    max_results_per_search: int
    businesses_found: int
    new_leads: int
    duplicate_leads: int
    errors_count: int
    current_search_id: str | None
    current_query: str
    worker_status: str
    error_message: str
    started_at: datetime | None
    finished_at: datetime | None
    searches: list[ScrapingSearchOut] = []
    campaign_id: str | None = None
    campaign_status: str = ""
    campaign_sent_today: int = 0
    campaign_daily_limit: int = 0
    skipped_no_email: int = 0
    analyzed_count: int = 0
    finding_emails: bool = False
    outreach: dict[str, Any] | None = None

    model_config = {"from_attributes": True}
