"""local risk check of each recipient address (format, sending history, domain DNS; not mailbox verification)

Revision ID: 0035_address_checks
Revises: 0034_safe_addresses_only
Create Date: 2026-10-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0035_address_checks"
down_revision = "0034_safe_addresses_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "address_checks",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False, server_default=""),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("checks", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "email", name="uq_address_check_user_email"),
    )
    op.create_index("ix_address_checks_user_id", "address_checks", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_address_checks_user_id", table_name="address_checks")
    op.drop_table("address_checks")
