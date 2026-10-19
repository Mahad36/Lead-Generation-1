"""optional mailbox verification before audits: provider, key, scope, monthly limit, usage

Revision ID: 0030_email_verifier
Revises: 0029_automation_offers
Create Date: 2026-10-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0030_email_verifier"
down_revision = "0029_automation_offers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("email_verifier_provider", sa.String(32), nullable=False, server_default=""))
    op.add_column("user_settings", sa.Column("encrypted_email_verifier_key", sa.Text(), nullable=False, server_default=""))
    op.add_column("user_settings", sa.Column("email_verifier_scope", sa.String(16), nullable=False, server_default="risky"))
    op.add_column("user_settings", sa.Column("email_verifier_monthly_limit", sa.Integer(), nullable=False, server_default="100"))
    op.add_column("user_settings", sa.Column("email_verifier_usage", JSONB(), nullable=False, server_default="{}"))


def downgrade() -> None:
    for column in ("email_verifier_usage", "email_verifier_monthly_limit", "email_verifier_scope",
                   "encrypted_email_verifier_key", "email_verifier_provider"):
        op.drop_column("user_settings", column)
