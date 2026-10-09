from typing import Literal

from pydantic import BaseModel, Field


class UserServiceIn(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    is_active: bool = True
    sort_order: int = 0


class UserServiceOut(UserServiceIn):
    id: str

    model_config = {"from_attributes": True}


class SettingsOut(BaseModel):
    sender_name: str
    company_name: str
    company_website: str
    company_description: str
    free_offers: str = ""
    track_opens: bool = True
    portfolio_sheet_url: str = ""
    contact_email: str
    contact_phone: str
    default_signature: str
    target_industries: str = ""
    scrape_max_results: int
    crawl_max_pages: int
    crawl_timeout_seconds: int
    headless_browser: bool
    ai_provider: str
    ai_model: str
    ai_temperature: float
    ai_api_base: str = ""
    ai_api_key_set: bool = False
    ai_configured: bool
    apify_configured: bool = False
    apify_token_set: bool = False
    auto_audit_enabled: bool = False
    auto_audit_daily_limit: int = 10
    auto_audit_send: bool = True
    scraper_provider: str = "scraper"
    default_daily_send_limit: int
    default_hourly_send_limit: int
    default_min_send_delay_seconds: int
    default_max_send_delay_seconds: int
    require_approval_before_sending: bool
    skip_previously_contacted: bool
    require_verified_emails: bool
    respect_suppression_list: bool
    google_oauth_configured: bool
    google_client_id: str = ""
    google_client_secret_set: bool = False
    google_redirect_uri: str = ""
    google_redirect_uris: list[str] = []
    services: list[UserServiceOut] = []

    model_config = {"from_attributes": True}


class SettingsUpdate(BaseModel):
    sender_name: str | None = None
    company_name: str | None = None
    company_website: str | None = None
    company_description: str | None = None
    free_offers: str | None = None
    track_opens: bool | None = None
    portfolio_sheet_url: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    default_signature: str | None = None
    target_industries: str | None = None
    scrape_max_results: int | None = Field(default=None, ge=1)
    crawl_max_pages: int | None = Field(default=None, ge=1, le=20)
    crawl_timeout_seconds: int | None = Field(default=None, ge=5, le=120)
    headless_browser: bool | None = None
    ai_provider: str | None = None
    ai_model: str | None = None
    ai_temperature: float | None = Field(default=None, ge=0, le=2)
    ai_api_base: str | None = None
    ai_api_key: str | None = None
    google_client_id: str | None = None
    google_client_secret: str | None = None
    apify_token: str | None = None
    scraper_provider: Literal["scraper", "apify"] | None = None
    auto_audit_enabled: bool | None = None
    auto_audit_daily_limit: int | None = Field(default=None, ge=1, le=200)
    auto_audit_send: bool | None = None
    default_daily_send_limit: int | None = None
    default_hourly_send_limit: int | None = None
    default_min_send_delay_seconds: int | None = None
    default_max_send_delay_seconds: int | None = None
    require_approval_before_sending: bool | None = None
    skip_previously_contacted: bool | None = None
    require_verified_emails: bool | None = None
    respect_suppression_list: bool | None = None
    services: list[UserServiceIn] | None = None
