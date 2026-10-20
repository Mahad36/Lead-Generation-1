"""only email address types with a low chance of bouncing (risky types need a verification service's confirmation)

Revision ID: 0034_safe_addresses_only
Revises: 0033_role_based_fix
Create Date: 2026-10-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0034_safe_addresses_only"
down_revision = "0033_role_based_fix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("safe_addresses_only", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("user_settings", "safe_addresses_only")
