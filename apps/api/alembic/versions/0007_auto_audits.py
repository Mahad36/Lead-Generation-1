"""automatic audit poller settings

Revision ID: 0007_auto_audits
Revises: 0006_user_apify_token
Create Date: 2026-10-06
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_auto_audits"
down_revision = "0006_user_apify_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("auto_audit_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("user_settings", sa.Column("auto_audit_daily_limit", sa.Integer(), nullable=False, server_default="10"))
    op.add_column("user_settings", sa.Column("auto_audit_send", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("website_audits", sa.Column("trigger", sa.String(16), nullable=False, server_default="manual"))
    op.create_index("ix_website_audits_trigger", "website_audits", ["trigger"])


def downgrade() -> None:
    op.drop_index("ix_website_audits_trigger", table_name="website_audits")
    op.drop_column("website_audits", "trigger")
    op.drop_column("user_settings", "auto_audit_send")
    op.drop_column("user_settings", "auto_audit_daily_limit")
    op.drop_column("user_settings", "auto_audit_enabled")
