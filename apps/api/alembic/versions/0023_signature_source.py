"""where each account's signature comes from: read from Gmail, an uploaded HTML file, or none

Revision ID: 0023_signature_source
Revises: 0022_business_contacts
Create Date: 2026-10-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_signature_source"
down_revision = "0022_business_contacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_accounts", sa.Column("signature_source", sa.String(16), nullable=False, server_default="gmail"))


def downgrade() -> None:
    op.drop_column("email_accounts", "signature_source")
