from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class EmailAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "email_accounts"
    __table_args__ = (UniqueConstraint("user_id", "email_address", name="uq_email_account_user_address"),)

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), default="gmail", nullable=False)
    email_address: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="connected", nullable=False)
    encrypted_credentials: Mapped[str] = mapped_column(Text, default="", nullable=False)
    scopes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    daily_limit: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    sent_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_today_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_successful_send_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # IMAP reply/bounce check progress (SMTP accounts)
    inbox_uidvalidity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    inbox_last_uid: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    inbox_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    inbox_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Sent folder progress: replies you write in Gmail itself are added to the lead's conversation too
    sent_folder: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    sent_uidvalidity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sent_last_uid: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Ramp-up: a new inbox starts low and rises each day to daily_limit (None: already warmed up)
    ramp_started_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Paused automatically (too many bounces) until you resume it in Email setup
    paused_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warmup_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Last spam-placement test and domain (SPF / DKIM / DMARC) check
    health: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # This Gmail's own signature, read from the latest email written in Gmail (services/gmail_signature.py).
    # SMTP / API sends never get Gmail's signature automatically, so the app adds this one.
    gmail_signature_html: Mapped[str] = mapped_column(Text, default="", nullable=False)
    gmail_signature_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    gmail_signature_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gmail_signature_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gmail_signature_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # gmail: read from emails written in Gmail | uploaded: an HTML file you uploaded (never overwritten) | none
    signature_source: Mapped[str] = mapped_column(String(16), default="gmail", nullable=False)
    # Sending health from available evidence (services/account_health.py): healthy | monitoring | reduced |
    # paused | reconnect. A summary of what the app can observe, not Gmail's private reputation.
    health_status: Mapped[str] = mapped_column(String(32), default="monitoring", nullable=False)
    health_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    health_signals: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    recommended_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)  # today's safe capacity
    restricted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # backoff
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user = relationship("User")


class EmailTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "email_templates"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    html_body: Mapped[str] = mapped_column(Text, nullable=False)
    text_body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class EmailMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "email_messages"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    campaign_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("campaigns.id", ondelete="SET NULL"), index=True, nullable=True
    )
    campaign_lead_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("campaign_leads.id", ondelete="SET NULL"), nullable=True
    )
    lead_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("leads.id", ondelete="SET NULL"), index=True, nullable=True
    )
    email_account_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("email_accounts.id", ondelete="SET NULL"), nullable=True
    )
    recipient_email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    recipient_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    html_body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    text_body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True, nullable=False)
    provider_message_id: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # The Message-ID header we sent, to match replies (In-Reply-To / References) and bounces
    rfc_message_id: Mapped[str] = mapped_column(String(255), default="", index=True, nullable=False)
    open_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bounced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    click_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Follow-ups: their own rows (tracking, daily limit) pointing at the first email of the thread
    followup_of_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("email_messages.id", ondelete="CASCADE"), index=True, nullable=True
    )
    followup_step: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # First emails: follow-ups go out after it (decided when it was sent); done = finished or stopped
    followups_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    followups_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Additional business recipients on the same domain (campaign option "Additional Business Recipients (CC)")
    cc_emails: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # Failed sends: temporary | quota | rate_limited | auth | recipient | rejected | other
    error_kind: Mapped[str] = mapped_column(String(32), default="", nullable=False)

    events: Mapped[list["EmailEvent"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class ConversationMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One message in the conversation that follows an outreach email: a lead's reply, or your answer (sent from the
    app, or from Gmail and picked up from the Sent folder). The outreach email itself is the root."""

    __tablename__ = "conversation_messages"
    __table_args__ = (UniqueConstraint("user_id", "rfc_message_id", name="uq_conversation_rfc"),)

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    root_message_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("email_messages.id", ondelete="CASCADE"), index=True, nullable=False
    )
    lead_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("leads.id", ondelete="SET NULL"), index=True, nullable=True
    )
    email_account_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("email_accounts.id", ondelete="SET NULL"), nullable=True
    )
    # Your answer sent from the app is also an EmailMessage (open tracking, daily send limit)
    email_message_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("email_messages.id", ondelete="SET NULL"), nullable=True
    )
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # "in" (their reply) | "out" (your answer)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # "inbox" | "app" | "gmail" | "followup"
    from_email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    to_email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    subject: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    body_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    rfc_message_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmailEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "email_events"

    message_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("email_messages.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    message: Mapped[EmailMessage] = relationship(back_populates="events")


class SuppressionEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "suppression_list"
    __table_args__ = (UniqueConstraint("user_id", "email", name="uq_suppression_user_email"),)

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="manual", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PasswordResetToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "password_reset_tokens"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class WarmupMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A warm-up email between two of your own inboxes (kept apart from outreach numbers and limits)."""

    __tablename__ = "warmup_messages"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    from_account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("email_accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    to_account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=False
    )
    rfc_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    in_reply_to: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    subject: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    landed: Mapped[str] = mapped_column(String(16), default="", nullable=False)  # inbox | spam | promotions | missing
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class HealthAlert(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A warning that sending could hurt an inbox / domain (services/health_alerts.py). One open alert per key;
    resolved_at is set once the signal clears."""

    __tablename__ = "health_alerts"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_health_alert_user_key"),)

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    email_account_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=True
    )
    key: Mapped[str] = mapped_column(String(160), nullable=False)  # e.g. "bounces:<account id>", "dns:example.com"
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="warning", nullable=False)  # warning | critical
    subject: Mapped[str] = mapped_column(String(255), default="", nullable=False)  # the inbox or domain
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    advice: Mapped[str] = mapped_column(Text, default="", nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # pop-up shown
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
