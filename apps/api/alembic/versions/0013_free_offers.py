"""free offers the outreach email can make (one per line)

Revision ID: 0013_free_offers
Revises: 0012_import_linked_leads
Create Date: 2026-10-08
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_free_offers"
down_revision = "0012_import_linked_leads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("free_offers", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("user_settings", "free_offers")
