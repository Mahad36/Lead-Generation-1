from datetime import datetime

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ScrapingSearchIn(BaseModel):
    country: str = Field(min_length=1, max_length=128)
    city: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=255)


class ScheduleIn(BaseModel):
    """When automatic emails go out. "local": these days and hours in each lead's own time zone;
    "global": your sending hours (AI & settings). start_at: nothing goes out before this moment."""

    mode: Literal["local", "global"] = "local"
    days: list[int] = Field(default_factory=lambda: [1, 2, 3])  # Monday = 0 ... Sunday = 6
    start: int = Field(default=9, ge=0, le=23)
    end: int = Field(default=11, ge=1, le=24)
    start_at: datetime | None = None

    @field_validator("days")
    @classmethod
    def valid_days(cls, value: list[int]) -> list[int]:
        days = sorted({d for d in value if 0 <= d <= 6})
        if not days:
            raise ValueError("Pick at least one day to send on")
        return days

    def as_dict(self) -> dict:
        data = self.model_dump()
        data["start_at"] = self.start_at.isoformat() if self.start_at else None
        return data


class ScrapingJobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    searches: list[ScrapingSearchIn] = Field(min_length=1)
    max_results_per_search: int | None = Field(default=None, ge=1)
    auto_send: bool = True
    subject: str | None = None
    html_body: str | None = None
    # Automatic outreach for this file: "auto" (audit + send), "review" (audit, I send), "off"
    outreach_mode: Literal["auto", "review", "off"] = "off"
    outreach_daily_limit: int = Field(default=0, ge=0, le=100000)  # audits per day; 0 = no limit
    # The email account this file's outreach sends from (empty: your default account)
    outreach_email_account_id: str | None = None
    # Follow-ups (AI & settings -> Sending) after each first email from this file
    followups: bool = True
    # When this file's emails go out (None: your sending hours)
    schedule: ScheduleIn | None = None


class OutreachAction(BaseModel):
    action: Literal["start", "pause", "resume", "stop", "update", "retry_failed"]
    auto_send: bool | None = None
    daily_limit: int | None = Field(default=None, ge=0, le=100000)  # 0 = no daily audit limit
    # Send from this account ("" = your default account); left out = unchanged
    email_account_id: str | None = None


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
