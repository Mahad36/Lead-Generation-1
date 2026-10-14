"""business contacts: names, job titles and ranking for discovered emails; CC recipients on sent emails

Revision ID: 0022_business_contacts
Revises: 0021_account_health
Create Date: 2026-10-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0022_business_contacts"
down_revision = "0021_account_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lead_emails", sa.Column("contact_name", sa.String(255), nullable=False, server_default=""))
    op.add_column("lead_emails", sa.Column("job_title", sa.String(255), nullable=False, server_default=""))
    op.add_column("lead_emails", sa.Column("rank_reason", sa.Text(), nullable=False, server_default=""))
    op.add_column("lead_emails", sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_messages", sa.Column("cc_emails", JSONB(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("email_messages", "cc_emails")
    for name in ("discovered_at", "rank_reason", "job_title", "contact_name"):
        op.drop_column("lead_emails", name)
