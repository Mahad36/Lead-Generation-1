from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Campaign(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "campaigns"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email_account_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("email_accounts.id", ondelete="SET NULL"), nullable=True
    )
    template_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True, nullable=False)
    daily_limit: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    hourly_limit: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    min_delay_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    max_delay_seconds: Mapped[int] = mapped_column(Integer, default=180, nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    require_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    skip_previously_contacted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sent_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_today_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sent_this_hour: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_this_hour_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_eligible_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_reason: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    leads: Mapped[list["CampaignLead"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    email_account = relationship("EmailAccount")
    template = relationship("EmailTemplate")


class CampaignLead(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "campaign_leads"

    campaign_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    lead_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True, nullable=False)
    subject: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    html_body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    text_body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    personalized_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    skip_reason: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    campaign: Mapped[Campaign] = relationship(back_populates="leads")
    lead = relationship("Lead")
