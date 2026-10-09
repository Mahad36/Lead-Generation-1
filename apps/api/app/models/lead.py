from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Lead(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "leads"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scraping_job_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("scraping_jobs.id", ondelete="SET NULL"), nullable=True
    )
    import_batch_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("lead_imports.id", ondelete="SET NULL"), index=True, nullable=True
    )
    business_name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    contact_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(500), default="", index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(255), default="", index=True, nullable=False)
    country: Mapped[str] = mapped_column(String(128), default="", index=True, nullable=False)
    city: Mapped[str] = mapped_column(String(255), default="", index=True, nullable=False)
    address: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    normalized_address: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    google_maps_url: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    google_place_id: Mapped[str] = mapped_column(String(255), default="", index=True, nullable=False)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    website_url: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    website_domain: Mapped[str] = mapped_column(String(255), default="", index=True, nullable=False)
    primary_phone: Mapped[str] = mapped_column(String(64), default="", index=True, nullable=False)
    primary_email: Mapped[str] = mapped_column(String(320), default="", index=True, nullable=False)
    email_status: Mapped[str] = mapped_column(String(32), default="none", index=True, nullable=False)
    email_verification_status: Mapped[str] = mapped_column(
        String(32), default="not_checked", index=True, nullable=False
    )
    platform: Mapped[str] = mapped_column(String(64), default="", index=True, nullable=False)
    website_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lead_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lead_score_reasons: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="google_maps", nullable=False)
    source_search: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="new", index=True, nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    emails: Mapped[list["LeadEmail"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    phones: Mapped[list["LeadPhone"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    website: Mapped["Website | None"] = relationship(
        back_populates="lead", uselist=False, cascade="all, delete-orphan"
    )
    import_batch: Mapped["LeadImport | None"] = relationship(back_populates="leads")


class LeadImport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "lead_imports"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="processing", index=True, nullable=False)
    lead_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ready_leads: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_leads: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    analyzed_leads: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_leads: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invalid_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Rows that matched leads you already had: those leads are linked to this upload (not copied), so they still
    # count as this file's leads and go into its campaign.
    linked_lead_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    leads: Mapped[list["Lead"]] = relationship(back_populates="import_batch")


class LeadEmail(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "lead_emails"

    lead_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_url: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(32), default="not_checked", nullable=False
    )
    verification_details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    email_type: Mapped[str] = mapped_column(String(32), default="general", nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    lead: Mapped[Lead] = relationship(back_populates="emails")


class LeadPhone(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "lead_phones"

    lead_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    phone: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="google_maps", nullable=False)
    phone_type: Mapped[str] = mapped_column(String(32), default="main", nullable=False)

    lead: Mapped[Lead] = relationship(back_populates="phones")
