from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="owner", nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    settings: Mapped["UserSettings"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    services: Mapped[list["UserService"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserSettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    sender_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    company_website: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    company_description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Free things a cold email may offer (one per line); Claude picks the one that fits each site best
    free_offers: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Adds the open-tracking image to sent emails (on by default)
    track_opens: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Account health protection (services/send_protection.py): "strict" holds sending on any credible warning
    # sign; "balanced" only on serious ones (warnings halve capacity instead)
    health_policy: Mapped[str] = mapped_column(String(16), default="strict", server_default="strict", nullable=False)
    # Only email address types with a low chance of bouncing; riskier types need a verification service's confirmation
    safe_addresses_only: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    # What the outreach leads with: "automation" (automation first, website as support), "balanced" (website finding
    # plus one automation), "website" (website first)
    outreach_focus: Mapped[str] = mapped_column(String(16), default="balanced", server_default="balanced", nullable=False)
    # Automations you offer (services/automations.py keys, comma-separated): audits and emails only propose these
    automation_offers: Mapped[str] = mapped_column(
        Text,
        default="online_booking,instant_reply,ai_assistant,review_requests,quote_followup,missed_call_textback",
        server_default="online_booking,instant_reply,ai_assistant,review_requests,quote_followup,missed_call_textback",
        nullable=False,
    )
    # Google Sheet of sites you built ("Anyone with the link"): one is referenced in every email by niche
    portfolio_sheet_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    contact_email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    # Deliverability (all on by default): follow-ups, sending hours, address checks, warm-up
    followups_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    followup_days: Mapped[str] = mapped_column(String(64), default="3,5", nullable=False)  # days after the previous email
    send_window_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    send_window_start: Mapped[int] = mapped_column(Integer, default=8, nullable=False)  # local hour
    send_window_end: Mapped[int] = mapped_column(Integer, default=19, nullable=False)
    send_weekdays_only: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Daily limits reset at this local hour (0 = midnight; 17 for a 5 pm - 2 am working day)
    day_start_hour: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    check_email_domains: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    warmup_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    target_industries: Mapped[str] = mapped_column(Text, default="", nullable=False)

    scrape_max_results: Mapped[int] = mapped_column(Integer, default=200, nullable=False)
    crawl_max_pages: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    crawl_timeout_seconds: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    headless_browser: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    ai_provider: Mapped[str] = mapped_column(String(64), default="openai", nullable=False)
    ai_model: Mapped[str] = mapped_column(String(128), default="gpt-4o-mini", nullable=False)
    ai_temperature: Mapped[float] = mapped_column(default=0.4, nullable=False)
    ai_api_base: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    encrypted_ai_api_key: Mapped[str] = mapped_column(Text, default="", nullable=False)
    google_client_id: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    encrypted_google_client_secret: Mapped[str] = mapped_column(Text, default="", nullable=False)
    encrypted_apify_token: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Google Maps lead source: "scraper" (built-in, free) or "apify" (needs the token above)
    scraper_provider: Mapped[str] = mapped_column(String(16), default="scraper", nullable=False)

    default_daily_send_limit: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    default_hourly_send_limit: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    # Random delay between two emails from the same inbox (each inbox has its own)
    default_min_send_delay_seconds: Mapped[int] = mapped_column(Integer, default=180, nullable=False)
    default_max_send_delay_seconds: Mapped[int] = mapped_column(Integer, default=420, nullable=False)

    require_approval_before_sending: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    skip_previously_contacted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    require_verified_emails: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Optional mailbox verification before an audit (services/email_verification.py): "", "zerobounce", "hunter"
    email_verifier_provider: Mapped[str] = mapped_column(String(32), default="", server_default="", nullable=False)
    encrypted_email_verifier_key: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    # "own": your own checker (apps/verifier) at this address, e.g. https://verify.example.com
    email_verifier_url: Mapped[str] = mapped_column(String(500), default="", server_default="", nullable=False)
    email_verifier_scope: Mapped[str] = mapped_column(String(16), default="risky", server_default="risky", nullable=False)
    email_verifier_monthly_limit: Mapped[int] = mapped_column(Integer, default=100, server_default="100", nullable=False)
    # {"month": "2026-10", "used": 12, "exhausted": false}: stays within the provider's free allowance
    email_verifier_usage: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    respect_suppression_list: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Automatic audit poller: audits new leads and emails the report without manual steps
    auto_audit_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_audit_daily_limit: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    auto_audit_send: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped[User] = relationship(back_populates="settings")


class UserService(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_services"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_service_name"),)

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    user: Mapped[User] = relationship(back_populates="services")
