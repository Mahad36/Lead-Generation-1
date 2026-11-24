"""ZeroBounce "do_not_mail / role_based" addresses (info@, hello@) were stored as "valid": they're unconfirmed

ZeroBounce only labels a shared inbox as role-based; it doesn't confirm a mailbox exists (hello@nezevents.com was
role_based and bounced, no such user). Stored results become "unknown", so they're no longer emailed.

Revision ID: 0036_role_based_unconfirmed
Revises: 0035_address_checks
Create Date: 2026-11-04
"""

from alembic import op


revision = "0036_role_based_unconfirmed"
down_revision = "0035_address_checks"
branch_labels = None
depends_on = None

ROLE_BASED = "verification_details->>'detail' ILIKE 'do_not_mail / role_based'"


def upgrade() -> None:
    op.execute(f"""
        UPDATE leads SET email_verification_status = 'unknown'
        WHERE email_verification_status = 'valid' AND EXISTS (
            SELECT 1 FROM lead_emails le WHERE le.lead_id = leads.id AND lower(le.email) = lower(leads.primary_email)
              AND le.verification_status = 'valid' AND le.{ROLE_BASED})
    """)
    op.execute(f"""
        UPDATE lead_emails SET verification_status = 'unknown', is_verified = false
        WHERE verification_status = 'valid' AND {ROLE_BASED}
    """)


def downgrade() -> None:
    op.execute(f"""
        UPDATE lead_emails SET verification_status = 'valid', is_verified = true
        WHERE verification_status = 'unknown' AND {ROLE_BASED}
    """)
