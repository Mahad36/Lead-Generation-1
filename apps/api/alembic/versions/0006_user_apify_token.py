"""per-user Apify token for Google Maps scraping

Revision ID: 0006_user_apify_token
Revises: 0005_website_audits
Create Date: 2026-10-06
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_user_apify_token"
down_revision = "0005_website_audits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column("encrypted_apify_token", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("user_settings", "encrypted_apify_token")
