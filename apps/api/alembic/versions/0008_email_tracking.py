"""email open / reply / bounce tracking

Revision ID: 0008_email_tracking
Revises: 0007_auto_audits
Create Date: 2026-10-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_email_tracking"
down_revision = "0007_auto_audits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_messages", sa.Column("rfc_message_id", sa.String(255), nullable=False, server_default=""))
    op.add_column("email_messages", sa.Column("open_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("email_messages", sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_messages", sa.Column("last_opened_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_messages", sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_messages", sa.Column("bounced_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_email_messages_rfc_message_id", "email_messages", ["rfc_message_id"])
    op.add_column("email_accounts", sa.Column("inbox_uidvalidity", sa.BigInteger(), nullable=True))
    op.add_column("email_accounts", sa.Column("inbox_last_uid", sa.BigInteger(), nullable=True))
    op.add_column("email_accounts", sa.Column("inbox_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_accounts", sa.Column("inbox_error", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("email_accounts", "inbox_error")
    op.drop_column("email_accounts", "inbox_checked_at")
    op.drop_column("email_accounts", "inbox_last_uid")
    op.drop_column("email_accounts", "inbox_uidvalidity")
    op.drop_index("ix_email_messages_rfc_message_id", table_name="email_messages")
    op.drop_column("email_messages", "bounced_at")
    op.drop_column("email_messages", "replied_at")
    op.drop_column("email_messages", "last_opened_at")
    op.drop_column("email_messages", "opened_at")
    op.drop_column("email_messages", "open_count")
    op.drop_column("email_messages", "rfc_message_id")
