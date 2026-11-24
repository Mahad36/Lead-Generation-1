"""agency outreach: scrape files and their emails can be "agency" (white-label pitch with your company profile)

Revision ID: 0038_agency_outreach
Revises: 0037_followups_opened_only
Create Date: 2026-11-24
"""

from alembic import op
import sqlalchemy as sa


revision = "0038_agency_outreach"
down_revision = "0037_followups_opened_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scraping_jobs", sa.Column("purpose", sa.String(16), nullable=False, server_default="audit"))
    op.create_index("ix_scraping_jobs_purpose", "scraping_jobs", ["purpose"])
    op.add_column("website_audits", sa.Column("kind", sa.String(16), nullable=False, server_default="audit"))
    op.create_index("ix_website_audits_kind", "website_audits", ["kind"])
    op.add_column("user_settings", sa.Column("agency_pitch", sa.Text(), nullable=False, server_default=""))
    op.add_column("user_settings", sa.Column("agency_profile_filename", sa.String(255), nullable=False, server_default=""))
    op.add_column("user_settings", sa.Column("agency_profile_pdf", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_settings", "agency_profile_pdf")
    op.drop_column("user_settings", "agency_profile_filename")
    op.drop_column("user_settings", "agency_pitch")
    op.drop_index("ix_website_audits_kind", "website_audits")
    op.drop_column("website_audits", "kind")
    op.drop_index("ix_scraping_jobs_purpose", "scraping_jobs")
    op.drop_column("scraping_jobs", "purpose")
