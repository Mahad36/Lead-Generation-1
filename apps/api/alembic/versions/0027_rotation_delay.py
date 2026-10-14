"""rotation delay: each inbox's next allowed send time; 3-7 minute random delay per inbox by default

Revision ID: 0027_rotation_delay
Revises: 0026_health_protection
Create Date: 2026-10-15
"""

from alembic import op
import sqlalchemy as sa


revision = "0027_rotation_delay"
down_revision = "0026_health_protection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_accounts", sa.Column("next_send_at", sa.DateTime(timezone=True), nullable=True))
    # Settings still on the old untouched defaults (1-3 minutes) move to the new per-inbox default (3-7 minutes)
    op.execute(
        "UPDATE user_settings SET default_min_send_delay_seconds = 180, default_max_send_delay_seconds = 420 "
        "WHERE default_min_send_delay_seconds = 60 AND default_max_send_delay_seconds = 180"
    )


def downgrade() -> None:
    op.drop_column("email_accounts", "next_send_at")
