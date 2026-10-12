"""working day: the hour daily limits reset (e.g. 17 for a 5 pm - 2 am shift)

Revision ID: 0019_day_start_hour
Revises: 0018_deliverability
Create Date: 2026-10-09
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_day_start_hour"
down_revision = "0018_deliverability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("day_start_hour", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("user_settings", "day_start_hour")
