"""your own mailbox checker's address

Revision ID: 0031_own_verifier
Revises: 0030_email_verifier
Create Date: 2026-10-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0031_own_verifier"
down_revision = "0030_email_verifier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("email_verifier_url", sa.String(500), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("user_settings", "email_verifier_url")
