"""conversations: lead replies and your answers (from the app or Gmail), threaded under the outreach email

Revision ID: 0016_conversations
Revises: 0015_portfolio_sheet
Create Date: 2026-10-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0016_conversations"
down_revision = "0015_portfolio_sheet"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_messages",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("root_message_id", UUID(as_uuid=False), sa.ForeignKey("email_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=False), sa.ForeignKey("leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("email_account_id", UUID(as_uuid=False), sa.ForeignKey("email_accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("email_message_id", UUID(as_uuid=False), sa.ForeignKey("email_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("from_email", sa.String(320), nullable=False, server_default=""),
        sa.Column("to_email", sa.String(320), nullable=False, server_default=""),
        sa.Column("subject", sa.String(500), nullable=False, server_default=""),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("rfc_message_id", sa.String(255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "rfc_message_id", name="uq_conversation_rfc"),
    )
    op.create_index("ix_conversation_messages_user_id", "conversation_messages", ["user_id"])
    op.create_index("ix_conversation_messages_root_message_id", "conversation_messages", ["root_message_id"])
    op.create_index("ix_conversation_messages_lead_id", "conversation_messages", ["lead_id"])
    op.create_index("ix_conversation_messages_rfc_message_id", "conversation_messages", ["rfc_message_id"])
    op.create_index("ix_conversation_messages_occurred_at", "conversation_messages", ["occurred_at"])
    op.add_column("email_accounts", sa.Column("sent_folder", sa.String(255), nullable=False, server_default=""))
    op.add_column("email_accounts", sa.Column("sent_uidvalidity", sa.BigInteger(), nullable=True))
    op.add_column("email_accounts", sa.Column("sent_last_uid", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("email_accounts", "sent_last_uid")
    op.drop_column("email_accounts", "sent_uidvalidity")
    op.drop_column("email_accounts", "sent_folder")
    op.drop_table("conversation_messages")
