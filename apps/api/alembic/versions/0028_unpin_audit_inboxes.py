"""unpin unsent audit emails from the default inbox, so rotation picks the inbox when each one is sent

Audits from scrape files without a chosen inbox used to be created with the default inbox fixed on them, so every
email went from that one inbox and rotation never ran.

Revision ID: 0028_unpin_audit_inboxes
Revises: 0027_rotation_delay
Create Date: 2026-10-15
"""

from alembic import op


revision = "0028_unpin_audit_inboxes"
down_revision = "0027_rotation_delay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE website_audits w SET email_account_id = NULL
        FROM leads l, scraping_jobs j
        WHERE w.lead_id = l.id AND l.scraping_job_id = j.id
          AND w.trigger = 'auto' AND w.sent_at IS NULL AND w.status <> 'sending'
          AND w.email_account_id IS NOT NULL AND j.outreach_email_account_id IS NULL
        """
    )


def downgrade() -> None:
    pass
