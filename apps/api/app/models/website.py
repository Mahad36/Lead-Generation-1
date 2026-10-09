from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Website(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "websites"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    lead_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("leads.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    meta_description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    https: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reachable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    platform_name: Mapped[str] = mapped_column(String(64), default="unknown", index=True, nullable=False)
    platform_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    crawl_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    pages_crawled: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extracted_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    social_links: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    raw_headers: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    lead: Mapped["Lead"] = relationship(back_populates="website")
    analysis: Mapped["WebsiteAnalysis | None"] = relationship(
        back_populates="website", uselist=False, cascade="all, delete-orphan"
    )
    technologies: Mapped[list["WebsiteTechnology"]] = relationship(
        back_populates="website", cascade="all, delete-orphan"
    )


class WebsiteAnalysis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "website_analysis"

    website_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("websites.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    business_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    business_type: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    services: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    products: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    main_offering: Mapped[str] = mapped_column(Text, default="", nullable=False)
    target_audience: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    location: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    issues: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    opportunities: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    recommended_services: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    category_scores: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    website_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)

    website: Mapped[Website] = relationship(back_populates="analysis")


class WebsiteTechnology(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "website_technologies"

    website_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("websites.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    signals: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    is_primary_platform: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    website: Mapped[Website] = relationship(back_populates="technologies")
