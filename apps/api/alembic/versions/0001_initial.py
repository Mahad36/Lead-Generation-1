"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("role", sa.String(32), nullable=False, server_default="owner"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "user_settings",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("sender_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("company_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("company_website", sa.String(500), nullable=False, server_default=""),
        sa.Column("company_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("contact_email", sa.String(320), nullable=False, server_default=""),
        sa.Column("contact_phone", sa.String(64), nullable=False, server_default=""),
        sa.Column("default_signature", sa.Text(), nullable=False, server_default=""),
        sa.Column("scrape_max_results", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("crawl_max_pages", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("crawl_timeout_seconds", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("headless_browser", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ai_provider", sa.String(64), nullable=False, server_default="openai"),
        sa.Column("ai_model", sa.String(128), nullable=False, server_default="gpt-4o-mini"),
        sa.Column("ai_temperature", sa.Float(), nullable=False, server_default="0.4"),
        sa.Column("default_daily_send_limit", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("default_hourly_send_limit", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("default_min_send_delay_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("default_max_send_delay_seconds", sa.Integer(), nullable=False, server_default="180"),
        sa.Column("require_approval_before_sending", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("skip_previously_contacted", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("require_verified_emails", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("respect_suppression_list", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "user_services",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "name", name="uq_user_service_name"),
    )
    op.create_index("ix_user_services_user_id", "user_services", ["user_id"])

    op.create_table(
        "scraping_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False, server_default="Scraping job"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("max_results_per_search", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("businesses_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_leads", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_leads", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_search_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("current_query", sa.String(500), nullable=False, server_default=""),
        sa.Column("worker_status", sa.String(64), nullable=False, server_default="idle"),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extra", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_scraping_jobs_user_id", "scraping_jobs", ["user_id"])
    op.create_index("ix_scraping_jobs_status", "scraping_jobs", ["status"])

    op.create_table(
        "scraping_searches",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("scraping_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("country", sa.String(128), nullable=False),
        sa.Column("city", sa.String(255), nullable=False),
        sa.Column("category", sa.String(255), nullable=False),
        sa.Column("query", sa.String(500), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("businesses_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_leads", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_leads", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_page", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_scraping_searches_job_id", "scraping_searches", ["job_id"])

    op.create_table(
        "leads",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scraping_job_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("scraping_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("business_name", sa.String(500), nullable=False),
        sa.Column("normalized_name", sa.String(500), nullable=False, server_default=""),
        sa.Column("category", sa.String(255), nullable=False, server_default=""),
        sa.Column("country", sa.String(128), nullable=False, server_default=""),
        sa.Column("city", sa.String(255), nullable=False, server_default=""),
        sa.Column("address", sa.String(1000), nullable=False, server_default=""),
        sa.Column("normalized_address", sa.String(1000), nullable=False, server_default=""),
        sa.Column("google_maps_url", sa.String(2000), nullable=False, server_default=""),
        sa.Column("google_place_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("review_count", sa.Integer(), nullable=True),
        sa.Column("website_url", sa.String(2000), nullable=False, server_default=""),
        sa.Column("website_domain", sa.String(255), nullable=False, server_default=""),
        sa.Column("primary_phone", sa.String(64), nullable=False, server_default=""),
        sa.Column("primary_email", sa.String(320), nullable=False, server_default=""),
        sa.Column("email_status", sa.String(32), nullable=False, server_default="none"),
        sa.Column("email_verification_status", sa.String(32), nullable=False, server_default="not_checked"),
        sa.Column("platform", sa.String(64), nullable=False, server_default=""),
        sa.Column("website_score", sa.Integer(), nullable=True),
        sa.Column("lead_score", sa.Integer(), nullable=True),
        sa.Column("lead_score_reasons", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("do_not_contact", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(64), nullable=False, server_default="google_maps"),
        sa.Column("extra", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for col in (
        "user_id",
        "business_name",
        "normalized_name",
        "category",
        "country",
        "city",
        "google_place_id",
        "website_domain",
        "primary_phone",
        "primary_email",
        "email_status",
        "email_verification_status",
        "platform",
    ):
        op.create_index(f"ix_leads_{col}", "leads", [col])

    op.create_table(
        "lead_emails",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_url", sa.String(2000), nullable=False, server_default=""),
        sa.Column("verification_status", sa.String(32), nullable=False, server_default="not_checked"),
        sa.Column("verification_details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_lead_emails_lead_id", "lead_emails", ["lead_id"])
    op.create_index("ix_lead_emails_email", "lead_emails", ["email"])

    op.create_table(
        "lead_phones",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("phone", sa.String(64), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(64), nullable=False, server_default="google_maps"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_lead_phones_lead_id", "lead_phones", ["lead_id"])
    op.create_index("ix_lead_phones_phone", "lead_phones", ["phone"])

    op.create_table(
        "websites",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("url", sa.String(2000), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("title", sa.String(500), nullable=False, server_default=""),
        sa.Column("meta_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("https", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reachable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("platform_name", sa.String(64), nullable=False, server_default="unknown"),
        sa.Column("platform_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_crawled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_analyzed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("crawl_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("pages_crawled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extracted_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("social_links", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("raw_headers", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_websites_user_id", "websites", ["user_id"])
    op.create_index("ix_websites_domain", "websites", ["domain"])
    op.create_index("ix_websites_platform_name", "websites", ["platform_name"])

    op.create_table(
        "website_analysis",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("website_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("websites.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("raw_data", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("business_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("business_type", sa.String(255), nullable=False, server_default=""),
        sa.Column("services", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("products", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("main_offering", sa.Text(), nullable=False, server_default=""),
        sa.Column("target_audience", sa.String(500), nullable=False, server_default=""),
        sa.Column("location", sa.String(255), nullable=False, server_default=""),
        sa.Column("issues", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("opportunities", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("recommended_services", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("category_scores", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("website_score", sa.Integer(), nullable=True),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "website_technologies",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("website_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("websites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("signals", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_primary_platform", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_website_technologies_website_id", "website_technologies", ["website_id"])

    op.create_table(
        "email_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default="gmail"),
        sa.Column("email_address", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="connected"),
        sa.Column("encrypted_credentials", sa.Text(), nullable=False, server_default=""),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column("daily_limit", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("sent_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_today_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_send_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_email_accounts_user_id", "email_accounts", ["user_id"])
    op.create_index("ix_email_accounts_email_address", "email_accounts", ["email_address"])

    op.create_table(
        "email_templates",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_email_templates_user_id", "email_templates", ["user_id"])

    op.create_table(
        "campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email_account_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("email_accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("template_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("daily_limit", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("hourly_limit", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("min_delay_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("max_delay_seconds", sa.Integer(), nullable=False, server_default="180"),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("require_approval", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("skip_previously_contacted", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sent_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_today_date", sa.Date(), nullable=True),
        sa.Column("sent_this_hour", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_this_hour_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_eligible_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_reason", sa.String(255), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("extra", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_campaigns_user_id", "campaigns", ["user_id"])
    op.create_index("ix_campaigns_status", "campaigns", ["status"])

    op.create_table(
        "campaign_leads",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("subject", sa.String(500), nullable=False, server_default=""),
        sa.Column("html_body", sa.Text(), nullable=False, server_default=""),
        sa.Column("text_body", sa.Text(), nullable=False, server_default=""),
        sa.Column("personalized_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("skip_reason", sa.String(255), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_campaign_leads_campaign_id", "campaign_leads", ["campaign_id"])
    op.create_index("ix_campaign_leads_lead_id", "campaign_leads", ["lead_id"])
    op.create_index("ix_campaign_leads_status", "campaign_leads", ["status"])

    op.create_table(
        "email_messages",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True),
        sa.Column("campaign_lead_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("campaign_leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("email_account_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("email_accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recipient_email", sa.String(320), nullable=False),
        sa.Column("recipient_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False, server_default=""),
        sa.Column("text_body", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("provider_message_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_email_messages_user_id", "email_messages", ["user_id"])
    op.create_index("ix_email_messages_campaign_id", "email_messages", ["campaign_id"])
    op.create_index("ix_email_messages_lead_id", "email_messages", ["lead_id"])
    op.create_index("ix_email_messages_status", "email_messages", ["status"])
    op.create_index("ix_email_messages_recipient_email", "email_messages", ["recipient_email"])

    op.create_table(
        "email_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("email_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_email_events_message_id", "email_events", ["message_id"])

    op.create_table(
        "suppression_list",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("source", sa.String(64), nullable=False, server_default="manual"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "email", name="uq_suppression_user_email"),
    )
    op.create_index("ix_suppression_list_user_id", "suppression_list", ["user_id"])
    op.create_index("ix_suppression_list_email", "suppression_list", ["email"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False, server_default=""),
        sa.Column("entity_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("ip_address", sa.String(64), nullable=False, server_default=""),
        sa.Column("user_agent", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])


def downgrade() -> None:
    for table in [
        "audit_logs",
        "password_reset_tokens",
        "refresh_tokens",
        "suppression_list",
        "email_events",
        "email_messages",
        "campaign_leads",
        "campaigns",
        "email_templates",
        "email_accounts",
        "website_technologies",
        "website_analysis",
        "websites",
        "lead_phones",
        "lead_emails",
        "leads",
        "scraping_searches",
        "scraping_jobs",
        "user_services",
        "user_settings",
        "users",
    ]:
        op.drop_table(table)
