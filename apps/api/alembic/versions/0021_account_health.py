"""account health and capacity, retry backoff, rotation reporting

Revision ID: 0021_account_health
Revises: 0020_gmail_signatures
Create Date: 2026-10-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0021_account_health"
down_revision = "0020_gmail_signatures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Per-account sending state (services/account_health.py)
    op.add_column("email_accounts", sa.Column("health_status", sa.String(32), nullable=False, server_default="monitoring"))
    op.add_column("email_accounts", sa.Column("health_reason", sa.Text(), nullable=False, server_default=""))
    op.add_column("email_accounts", sa.Column("health_signals", JSONB(), nullable=False, server_default="{}"))
    op.add_column("email_accounts", sa.Column("recommended_limit", sa.Integer(), nullable=True))
    op.add_column("email_accounts", sa.Column("restricted_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_accounts", sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("email_accounts", sa.Column("health_checked_at", sa.DateTime(timezone=True), nullable=True))
    # Failed sends: whether Gmail's error was temporary (retry with backoff) or permanent (never retried)
    op.add_column("email_messages", sa.Column("error_kind", sa.String(32), nullable=False, server_default=""))
    op.add_column("website_audits", sa.Column("send_retryable", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("website_audits", "send_retryable")
    op.drop_column("email_messages", "error_kind")
    for name in (
        "health_checked_at",
        "consecutive_failures",
        "restricted_until",
        "recommended_limit",
        "health_signals",
        "health_reason",
        "health_status",
    ):
        op.drop_column("email_accounts", name)
