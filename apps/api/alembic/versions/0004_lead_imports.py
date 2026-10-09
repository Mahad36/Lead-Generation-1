"""lead import batches and ready status

Revision ID: 0004_lead_imports
Revises: 0003_user_integration_secrets
Create Date: 2026-09-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0004_lead_imports"
down_revision = "0003_user_integration_secrets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_imports",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="processing"),
        sa.Column("lead_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ready_leads", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_leads", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("analyzed_leads", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_leads", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invalid_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lead_imports_user_id", "lead_imports", ["user_id"])
    op.create_index("ix_lead_imports_status", "lead_imports", ["status"])
    op.add_column("leads", sa.Column("import_batch_id", postgresql.UUID(as_uuid=False), nullable=True))
    op.create_index("ix_leads_import_batch_id", "leads", ["import_batch_id"])
    op.create_foreign_key(
        "fk_leads_import_batch_id",
        "leads",
        "lead_imports",
        ["import_batch_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_leads_import_batch_id", "leads", type_="foreignkey")
    op.drop_index("ix_leads_import_batch_id", table_name="leads")
    op.drop_column("leads", "import_batch_id")
    op.drop_index("ix_lead_imports_status", table_name="lead_imports")
    op.drop_index("ix_lead_imports_user_id", table_name="lead_imports")
    op.drop_table("lead_imports")
