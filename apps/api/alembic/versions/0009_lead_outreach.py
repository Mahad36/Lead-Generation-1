"""lead outreach pipeline: contact name, click tracking, audit send retries

Revision ID: 0009_lead_outreach
Revises: 0008_email_tracking
Create Date: 2026-10-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_lead_outreach"
down_revision = "0008_email_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("contact_name", sa.String(255), nullable=False, server_default=""))
    op.add_column("email_messages", sa.Column("click_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("email_messages", sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_messages", sa.Column("last_clicked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("website_audits", sa.Column("send_attempts", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("website_audits", "send_attempts")
    op.drop_column("email_messages", "last_clicked_at")
    op.drop_column("email_messages", "clicked_at")
    op.drop_column("email_messages", "click_count")
    op.drop_column("leads", "contact_name")
