"""account health protection: your policy, and warnings you've reviewed

Revision ID: 0026_health_protection
Revises: 0025_health_alerts
Create Date: 2026-10-15
"""

from alembic import op
import sqlalchemy as sa


revision = "0026_health_protection"
down_revision = "0025_health_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("health_policy", sa.String(16), nullable=False, server_default="strict"))
    op.add_column("health_alerts", sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("health_alerts", "acknowledged_at")
    op.drop_column("user_settings", "health_policy")
