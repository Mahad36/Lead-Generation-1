"""website audits (PDF audit + outreach email workflow)

Revision ID: 0005_website_audits
Revises: 0004_lead_imports
Create Date: 2026-10-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0005_website_audits"
down_revision = "0004_lead_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "website_audits",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("email_account_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("email_message_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("step", sa.String(255), nullable=False, server_default=""),
        sa.Column("failed_step", sa.String(32), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("website_url", sa.String(2000), nullable=False),
        sa.Column("recipient_email", sa.String(320), nullable=False, server_default=""),
        sa.Column("auto_send", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("site_data", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("performance", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("report", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("overall_score", sa.Integer(), nullable=True),
        sa.Column("ai_provider", sa.String(64), nullable=False, server_default=""),
        sa.Column("ai_model", sa.String(128), nullable=False, server_default=""),
        sa.Column("pdf_data", sa.LargeBinary(), nullable=True),
        sa.Column("pdf_filename", sa.String(255), nullable=False, server_default=""),
        sa.Column("pdf_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("email_subject", sa.String(500), nullable=False, server_default=""),
        sa.Column("email_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["email_account_id"], ["email_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["email_message_id"], ["email_messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_website_audits_user_id", "website_audits", ["user_id"])
    op.create_index("ix_website_audits_lead_id", "website_audits", ["lead_id"])
    op.create_index("ix_website_audits_status", "website_audits", ["status"])


def downgrade() -> None:
    op.drop_index("ix_website_audits_status", table_name="website_audits")
    op.drop_index("ix_website_audits_lead_id", table_name="website_audits")
    op.drop_index("ix_website_audits_user_id", table_name="website_audits")
    op.drop_table("website_audits")
