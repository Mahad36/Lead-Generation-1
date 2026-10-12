"""file outreach: the email account a scrape file's outreach sends from

Revision ID: 0017_file_outreach_account
Revises: 0016_conversations
Create Date: 2026-10-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0017_file_outreach_account"
down_revision = "0016_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scraping_jobs",
        sa.Column(
            "outreach_email_account_id",
            UUID(as_uuid=False),
            sa.ForeignKey("email_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("scraping_jobs", "outreach_email_account_id")
