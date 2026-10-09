"""open tracking becomes opt-in: plain personal emails land in Gmail's Primary tab

Revision ID: 0014_track_opens
Revises: 0013_free_offers
Create Date: 2026-10-08
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_track_opens"
down_revision = "0013_free_offers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("track_opens", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("user_settings", "track_opens")
