from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8-sig",  # tolerate a byte-order mark from Windows editors
        extra="ignore",
    )

    secret_key: str = "change-me-to-a-long-random-secret"
    encryption_key: str = ""
    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"
    api_prefix: str = "/api"

    database_url: str = "postgresql+psycopg://leadgen:leadgen@localhost:5432/leadgen"

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14
    require_email_verification: bool = False

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/email-accounts/gmail/callback"
    google_oauth_scopes: str = (
        "https://www.googleapis.com/auth/gmail.send "
        "https://www.googleapis.com/auth/userinfo.email "
        "https://www.googleapis.com/auth/userinfo.profile "
        "openid"
    )

    ai_provider: str = "openai"
    ai_api_key: str = ""
    ai_api_base: str = "https://api.openai.com/v1"
    ai_model: str = "gpt-4o-mini"
    ai_temperature: float = 0.4
    ai_timeout_seconds: int = 60
    # "Claude Code" AI provider: the local `claude` CLI on the user's Claude subscription (no API key)
    claude_code_bin: str = "claude"
    claude_code_oauth_token: str = ""  # from `claude setup-token`; env CLAUDE_CODE_OAUTH_TOKEN
    claude_code_timeout_seconds: int = 600
    # Automatic audits: how often the poller looks for new leads, and how many it starts per check
    auto_audit_poll_seconds: int = 300
    auto_audit_batch_size: int = 2
    # Run the poller inside the API process (no Redis/Celery), used by scripts/run_local.py
    inline_scheduler: bool = False

    headless: bool = True
    browser: str = "chrome"
    chromedriver_path: str = ""
    # Path to the Chrome/Chromium executable when it isn't installed as "google-chrome" (e.g. /usr/bin/chromium)
    chrome_binary: str = ""
    selenium_page_load_timeout: int = 45
    selenium_implicit_wait: int = 0
    scrape_max_results: int = 25
    scrape_scroll_pause_seconds: float = 0.4
    scrape_min_delay: float = 0.15
    scrape_max_delay: float = 0.4
    apify_token: str = ""
    # Default lead source for users who haven't chosen one in settings: "scraper" (built-in, free) or "apify"
    # ("browser" = "scraper"; "auto" = Apify when a token is set)
    scraper_provider: Literal["scraper", "apify", "browser", "auto"] = "scraper"
    # Built-in scraper: business websites checked in parallel for emails, pages per site, seconds per page
    scraper_site_workers: int = 10
    scraper_site_pages: int = 3
    scraper_site_timeout_seconds: int = 8
    # Place pages opened at once for results whose card shows no phone / website
    scraper_place_tabs: int = 4
    apify_actor_id: str = "compass~crawler-google-places"

    audit_ai_timeout_seconds: int = 240
    # Where audit screenshots are kept so the PDF can be rebuilt later (empty = <system temp>/leadgen-audits)
    audit_files_dir: str = ""
    # Automatic retries: a failed audit is retried after a cooldown, a failed send a few times
    auto_audit_max_attempts: int = 2
    auto_audit_retry_minutes: int = 30
    auto_send_max_attempts: int = 3

    crawl_max_pages: int = 5
    crawl_timeout_seconds: int = 20
    crawl_respect_robots: bool = True
    crawl_user_agent: str = "LeadGeneratorBot/1.0 (+https://localhost)"

    default_daily_send_limit: int = 50
    default_hourly_send_limit: int = 20
    default_min_send_delay_seconds: int = 180  # per inbox: 3-7 minutes, random
    default_max_send_delay_seconds: int = 420
    max_allowed_daily_send_limit: int = 500
    max_allowed_hourly_send_limit: int = 80
    min_allowed_send_delay_seconds: int = 15

    email_verifier_provider: str = ""
    email_verifier_api_key: str = ""
    # Public https address of this app's /t/ routes (e.g. a Cloudflare Tunnel: https://t.example.com). Used for the
    # open-tracking image and the unsubscribe link in emails; without it opens aren't tracked and the unsubscribe
    # link falls back to FRONTEND_URL.
    public_url: str = ""
    enable_email_open_tracking: bool = True
    # Route links in sent emails through PUBLIC_URL/t/c/... to count clicks (the unsubscribe link is never rewritten)
    enable_email_click_tracking: bool = True
    # How often connected SMTP inboxes are checked over IMAP for replies and bounces (0 = off)
    inbox_check_seconds: int = 300

    @field_validator("encryption_key")
    @classmethod
    def strip_key(cls, value: str) -> str:
        return value.strip()

    @property
    def google_scopes_list(self) -> list[str]:
        return [s for s in self.google_oauth_scopes.split() if s]

    @property
    def cors_origins(self) -> list[str]:
        return [
            self.frontend_url,
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
            "http://localhost:3002",
            "http://127.0.0.1:3002",
        ]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def ai_configured(self) -> bool:
        return bool(self.ai_api_key)

    @property
    def apify_configured(self) -> bool:
        return bool(self.apify_token)

    @property
    def google_oauth_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
