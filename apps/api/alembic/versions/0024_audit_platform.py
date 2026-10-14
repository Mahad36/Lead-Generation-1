"""platform intelligence on audits: platform, confidence, finding types (for filters), editable outreach angle

Details (secondary technologies, detection evidence, feature checks) live in website_audits.site_data
["platform_intel"]; platform opportunities in report["platform_opportunities"].

Revision ID: 0024_audit_platform
Revises: 0023_signature_source
Create Date: 2026-10-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0024_audit_platform"
down_revision = "0023_signature_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("website_audits", sa.Column("platform", sa.String(64), nullable=False, server_default=""))
    op.add_column("website_audits", sa.Column("platform_confidence", sa.Float(), nullable=True))
    op.add_column("website_audits", sa.Column("finding_types", JSONB(), nullable=False, server_default="[]"))
    op.add_column("website_audits", sa.Column("outreach_angle", sa.Text(), nullable=False, server_default=""))
    op.create_index("ix_website_audits_platform", "website_audits", ["platform"])
    # Existing audits: their detected platform, so the platform filter works for them too
    op.execute(
        "UPDATE website_audits SET platform = COALESCE(NULLIF(site_data->>'platform', ''), '') WHERE platform = ''"
    )


def downgrade() -> None:
    op.drop_index("ix_website_audits_platform", table_name="website_audits")
    for name in ("outreach_angle", "finding_types", "platform_confidence", "platform"):
        op.drop_column("website_audits", name)
