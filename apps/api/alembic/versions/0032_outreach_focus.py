"""outreach focus: what emails lead with (automation / balanced / website); website issues first, then one
automation ("balanced") by default

Revision ID: 0032_outreach_focus
Revises: 0031_own_verifier
Create Date: 2026-10-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0032_outreach_focus"
down_revision = "0031_own_verifier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("outreach_focus", sa.String(16), nullable=False, server_default="balanced"))


def downgrade() -> None:
    op.drop_column("user_settings", "outreach_focus")
