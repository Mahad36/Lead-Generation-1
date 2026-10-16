"""automation offers: which automations you sell (audits and emails only propose these)

Revision ID: 0029_automation_offers
Revises: 0028_unpin_audit_inboxes
Create Date: 2026-10-16
"""

from alembic import op
import sqlalchemy as sa


revision = "0029_automation_offers"
down_revision = "0028_unpin_audit_inboxes"
branch_labels = None
depends_on = None

ALL = "online_booking,instant_reply,ai_assistant,review_requests,quote_followup,missed_call_textback"


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("automation_offers", sa.Text(), nullable=False, server_default=ALL))


def downgrade() -> None:
    op.drop_column("user_settings", "automation_offers")
