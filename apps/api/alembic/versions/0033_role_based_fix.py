"""ZeroBounce "role_based" answers (info@, office@ on the business's own domain) were read as dead mailboxes:
restore those addresses and their leads

Revision ID: 0033_role_based_fix
Revises: 0032_outreach_focus
Create Date: 2026-10-20
"""

from alembic import op


revision = "0033_role_based_fix"
down_revision = "0032_outreach_focus"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only addresses on the website's own domain (a role-based address at another company stays skipped)
    op.execute(
        """
        UPDATE lead_emails le SET verification_status = 'valid'
        FROM leads l
        WHERE le.lead_id = l.id
          AND le.verification_status = 'invalid'
          AND le.verification_details->>'provider' = 'zerobounce'
          AND le.verification_details->>'detail' LIKE 'do_not_mail / role_based%'
          AND split_part(lower(le.email), '@', 2) =
              split_part(regexp_replace(lower(l.website_url), '^https?://(www\.)?', ''), '/', 1)
        """
    )
    op.execute(
        """
        UPDATE leads l SET email_status = 'found', email_verification_status = 'valid'
        FROM lead_emails le
        WHERE le.lead_id = l.id AND lower(le.email) = lower(l.primary_email)
          AND le.verification_status = 'valid'
          AND le.verification_details->>'detail' LIKE 'do_not_mail / role_based%'
          AND l.email_status = 'undeliverable'
        """
    )


def downgrade() -> None:
    pass
