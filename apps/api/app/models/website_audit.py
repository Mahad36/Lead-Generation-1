from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, deferred, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class WebsiteAudit(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One run of the audit workflow: crawl -> performance -> Claude report -> PDF -> email."""

    __tablename__ = "website_audits"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    lead_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    email_account_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("email_accounts.id", ondelete="SET NULL"), nullable=True
    )
    email_message_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("email_messages.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True, nullable=False)
    step: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    failed_step: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    website_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    auto_send: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), default="manual", index=True, nullable=False)
    send_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # False after a permanent send failure (bad address, rejected): never retried automatically
    send_retryable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    site_data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    performance: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    report: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    overall_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_provider: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    ai_model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    pdf_data: Mapped[bytes | None] = deferred(mapped_column(LargeBinary, nullable=True))
    pdf_filename: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    pdf_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    email_subject: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    email_html: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
