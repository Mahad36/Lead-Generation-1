"""per-user AI and Google OAuth credentials

Revision ID: 0003_user_integration_secrets
Revises: 0002_saas_fields
Create Date: 2026-09-13
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_user_integration_secrets"
down_revision = "0002_saas_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("ai_api_base", sa.String(500), nullable=False, server_default=""))
    op.add_column("user_settings", sa.Column("encrypted_ai_api_key", sa.Text(), nullable=False, server_default=""))
    op.add_column("user_settings", sa.Column("google_client_id", sa.String(255), nullable=False, server_default=""))
    op.add_column(
        "user_settings",
        sa.Column("encrypted_google_client_secret", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("user_settings", "encrypted_google_client_secret")
    op.drop_column("user_settings", "google_client_id")
    op.drop_column("user_settings", "encrypted_ai_api_key")
    op.drop_column("user_settings", "ai_api_base")
