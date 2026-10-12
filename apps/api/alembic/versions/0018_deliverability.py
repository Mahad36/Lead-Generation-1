"""deliverability: follow-ups, inbox rotation, ramp-up, bounce auto-pause, sending hours, address checks, warm-up

Revision ID: 0018_deliverability
Revises: 0017_file_outreach_account
Create Date: 2026-10-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0018_deliverability"
down_revision = "0017_file_outreach_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Settings (AI & settings -> Sending): every feature on by default
    for name, column in [
        ("followups_enabled", sa.Column("followups_enabled", sa.Boolean(), nullable=False, server_default=sa.true())),
        ("followup_days", sa.Column("followup_days", sa.String(64), nullable=False, server_default="3,5")),
        ("send_window_enabled", sa.Column("send_window_enabled", sa.Boolean(), nullable=False, server_default=sa.true())),
        ("send_window_start", sa.Column("send_window_start", sa.Integer(), nullable=False, server_default="8")),
        ("send_window_end", sa.Column("send_window_end", sa.Integer(), nullable=False, server_default="19")),
        ("send_weekdays_only", sa.Column("send_weekdays_only", sa.Boolean(), nullable=False, server_default=sa.true())),
        ("check_email_domains", sa.Column("check_email_domains", sa.Boolean(), nullable=False, server_default=sa.true())),
        ("warmup_enabled", sa.Column("warmup_enabled", sa.Boolean(), nullable=False, server_default=sa.true())),
    ]:
        op.add_column("user_settings", column)

    # Email accounts: ramp-up for new inboxes, bounce auto-pause, warm-up, last health checks
    op.add_column("email_accounts", sa.Column("ramp_started_on", sa.Date(), nullable=True))
    op.add_column("email_accounts", sa.Column("paused_reason", sa.Text(), nullable=False, server_default=""))
    op.add_column("email_accounts", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_accounts", sa.Column("warmup_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("email_accounts", sa.Column("health", JSONB(), nullable=False, server_default="{}"))

    # Email messages: follow-ups are their own rows (tracking, daily limit) pointing at the first email
    op.add_column(
        "email_messages",
        sa.Column(
            "followup_of_id",
            UUID(as_uuid=False),
            sa.ForeignKey("email_messages.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_email_messages_followup_of_id", "email_messages", ["followup_of_id"])
    op.add_column("email_messages", sa.Column("followup_step", sa.Integer(), nullable=False, server_default="0"))
    # First emails: whether follow-ups go out after it (from settings + the campaign / file switch at send time)
    op.add_column("email_messages", sa.Column("followups_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("email_messages", sa.Column("followups_done_at", sa.DateTime(timezone=True), nullable=True))

    # Warm-up emails between your own inboxes (kept apart from outreach numbers)
    op.create_table(
        "warmup_messages",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_account_id", UUID(as_uuid=False), sa.ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_account_id", UUID(as_uuid=False), sa.ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rfc_message_id", sa.String(255), nullable=False),
        sa.Column("in_reply_to", sa.String(255), nullable=False, server_default=""),
        sa.Column("subject", sa.String(500), nullable=False, server_default=""),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("landed", sa.String(16), nullable=False, server_default=""),  # inbox | spam | promotions | missing
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_warmup_messages_user_id", "warmup_messages", ["user_id"])
    op.create_index("ix_warmup_messages_from_account_id", "warmup_messages", ["from_account_id"])
    op.create_index("ix_warmup_messages_sent_at", "warmup_messages", ["sent_at"])


def downgrade() -> None:
    op.drop_table("warmup_messages")
    for name in ("followups_done_at", "followups_enabled", "followup_step"):
        op.drop_column("email_messages", name)
    op.drop_index("ix_email_messages_followup_of_id", table_name="email_messages")
    op.drop_column("email_messages", "followup_of_id")
    for name in ("health", "warmup_enabled", "paused_at", "paused_reason", "ramp_started_on"):
        op.drop_column("email_accounts", name)
    for name in (
        "warmup_enabled",
        "check_email_domains",
        "send_weekdays_only",
        "send_window_end",
        "send_window_start",
        "send_window_enabled",
        "followup_days",
        "followups_enabled",
    ):
        op.drop_column("user_settings", name)
