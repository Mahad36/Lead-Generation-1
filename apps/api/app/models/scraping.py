from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ScrapingJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "scraping_jobs"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), default="Scraping job", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True, nullable=False)
    max_results_per_search: Mapped[int] = mapped_column(Integer, default=200, nullable=False)
    businesses_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    new_leads: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_leads: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    errors_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_search_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    current_query: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    worker_status: Mapped[str] = mapped_column(String(64), default="idle", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # Automatic outreach for this file only: "off" | "running" | "paused" | "stopped", its send mode and daily limit
    outreach_status: Mapped[str] = mapped_column(String(16), default="off", index=True, nullable=False)
    outreach_auto_send: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    outreach_daily_limit: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    # The email account this file's outreach sends from (none: your default account)
    outreach_email_account_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("email_accounts.id", ondelete="SET NULL"), nullable=True
    )

    searches: Mapped[list["ScrapingSearch"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="ScrapingSearch.sort_order"
    )


class ScrapingSearch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "scraping_searches"

    job_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("scraping_jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    city: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    query: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    businesses_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    new_leads: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_leads: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_page: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)

    job: Mapped[ScrapingJob] = relationship(back_populates="searches")
