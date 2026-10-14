"""health alerts: warnings that sending could hurt an inbox or domain

Revision ID: 0025_health_alerts
Revises: 0024_audit_platform
Create Date: 2026-10-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0025_health_alerts"
down_revision = "0024_audit_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "health_alerts",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "email_account_id", UUID(as_uuid=False), sa.ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("key", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("subject", sa.String(255), nullable=False, server_default=""),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("advice", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "key", name="uq_health_alert_user_key"),
    )
    op.create_index("ix_health_alerts_user_id", "health_alerts", ["user_id"])
    op.create_index("ix_health_alerts_resolved_at", "health_alerts", ["resolved_at"])


def downgrade() -> None:
    op.drop_table("health_alerts")
