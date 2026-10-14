"""signatures come from each Gmail account itself (read from emails written in Gmail); no in-app signature

Revision ID: 0020_gmail_signatures
Revises: 0019_day_start_hour
Create Date: 2026-10-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_gmail_signatures"
down_revision = "0019_day_start_hour"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_accounts", sa.Column("gmail_signature_html", sa.Text(), nullable=False, server_default=""))
    op.add_column("email_accounts", sa.Column("gmail_signature_text", sa.Text(), nullable=False, server_default=""))
    # Date of the email the signature was read from, and when the app last looked
    op.add_column("email_accounts", sa.Column("gmail_signature_from", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_accounts", sa.Column("gmail_signature_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_accounts", sa.Column("gmail_signature_error", sa.Text(), nullable=False, server_default=""))
    op.drop_column("user_settings", "default_signature")


def downgrade() -> None:
    op.add_column("user_settings", sa.Column("default_signature", sa.Text(), nullable=False, server_default=""))
    for name in (
        "gmail_signature_error",
        "gmail_signature_checked_at",
        "gmail_signature_from",
        "gmail_signature_text",
        "gmail_signature_html",
    ):
        op.drop_column("email_accounts", name)
