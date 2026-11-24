"""follow-ups go only to leads who opened an email of the thread (on by default)

Revision ID: 0037_followups_opened_only
Revises: 0036_role_based_unconfirmed
Create Date: 2026-11-05
"""

from alembic import op
import sqlalchemy as sa


revision = "0037_followups_opened_only"
down_revision = "0036_role_based_unconfirmed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column("followups_opened_only", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("user_settings", "followups_opened_only")
