"""per-user Google Maps lead source (built-in scraper or Apify)

Revision ID: 0010_scraper_provider
Revises: 0009_lead_outreach
Create Date: 2026-10-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_scraper_provider"
down_revision = "0009_lead_outreach"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_settings", sa.Column("scraper_provider", sa.String(16), nullable=False, server_default="scraper")
    )


def downgrade() -> None:
    op.drop_column("user_settings", "scraper_provider")
