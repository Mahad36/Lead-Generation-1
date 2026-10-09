"""portfolio Google Sheet: reference sites matched to each lead's niche

Revision ID: 0015_portfolio_sheet
Revises: 0014_track_opens
Create Date: 2026-10-08
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_portfolio_sheet"
down_revision = "0014_track_opens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("portfolio_sheet_url", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("user_settings", "portfolio_sheet_url")
