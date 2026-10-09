"""per-file automatic outreach for scrape jobs (own on/off, send mode, daily limit, pause/stop)

Revision ID: 0011_file_outreach
Revises: 0010_scraper_provider
Create Date: 2026-10-08
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_file_outreach"
down_revision = "0010_scraper_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing files start "off": nothing runs until the user starts outreach for that file.
    op.add_column("scraping_jobs", sa.Column("outreach_status", sa.String(16), nullable=False, server_default="off"))
    op.add_column("scraping_jobs", sa.Column("outreach_auto_send", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("scraping_jobs", sa.Column("outreach_daily_limit", sa.Integer(), nullable=False, server_default="10"))
    op.create_index("ix_scraping_jobs_outreach_status", "scraping_jobs", ["outreach_status"])


def downgrade() -> None:
    op.drop_index("ix_scraping_jobs_outreach_status", table_name="scraping_jobs")
    op.drop_column("scraping_jobs", "outreach_daily_limit")
    op.drop_column("scraping_jobs", "outreach_auto_send")
    op.drop_column("scraping_jobs", "outreach_status")
