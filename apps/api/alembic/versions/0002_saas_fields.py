"""saas multi-tenant field additions

Revision ID: 0002_saas_fields
Revises: 0001_initial
Create Date: 2026-09-12
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_saas_fields"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("target_industries", sa.Text(), nullable=False, server_default=""))
    op.add_column("email_accounts", sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("email_accounts", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_email_account_user_address", "email_accounts", ["user_id", "email_address"])
    op.add_column("leads", sa.Column("source_search", sa.String(500), nullable=False, server_default=""))
    op.add_column("leads", sa.Column("status", sa.String(32), nullable=False, server_default="new"))
    op.create_index("ix_leads_status", "leads", ["status"])
    op.add_column("lead_emails", sa.Column("email_type", sa.String(32), nullable=False, server_default="general"))
    op.add_column("lead_emails", sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("lead_phones", sa.Column("phone_type", sa.String(32), nullable=False, server_default="main"))


def downgrade() -> None:
    op.drop_column("lead_phones", "phone_type")
    op.drop_column("lead_emails", "is_verified")
    op.drop_column("lead_emails", "email_type")
    op.drop_index("ix_leads_status", table_name="leads")
    op.drop_column("leads", "status")
    op.drop_column("leads", "source_search")
    op.drop_constraint("uq_email_account_user_address", "email_accounts", type_="unique")
    op.drop_column("email_accounts", "last_used_at")
    op.drop_column("email_accounts", "is_default")
    op.drop_column("user_settings", "target_industries")
