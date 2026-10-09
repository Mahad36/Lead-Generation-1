"""uploaded files keep rows that matched existing leads (linked, not duplicated)

Revision ID: 0012_import_linked_leads
Revises: 0011_file_outreach
Create Date: 2026-10-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0012_import_linked_leads"
down_revision = "0011_file_outreach"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lead_imports", sa.Column("linked_lead_ids", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb"))
    )


def downgrade() -> None:
    op.drop_column("lead_imports", "linked_lead_ids")
