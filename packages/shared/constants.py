"""Shared domain constants. Keep frontend copies in apps/web/lib/constants.ts in sync."""

from enum import StrEnum


class UserRole(StrEnum):
    OWNER = "owner"
    MEMBER = "member"


class ScrapingJobStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    BLOCKED = "blocked"


class ScrapingSearchStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class EmailVerificationStatus(StrEnum):
    NOT_CHECKED = "not_checked"
    VALID = "valid"
    INVALID = "invalid"
    RISKY = "risky"
    UNKNOWN = "unknown"


class LeadEmailStatus(StrEnum):
    NONE = "none"
    FOUND = "found"
    VERIFIED = "verified"
    INVALID = "invalid"
    CONTACTED = "contacted"
    UNSUBSCRIBED = "unsubscribed"


class LeadStatus(StrEnum):
    NEW = "new"
    PROCESSING = "processing"
    READY = "ready"
    NO_EMAIL = "no_email"
    FAILED = "failed"


class LeadSource(StrEnum):
    GOOGLE_MAPS = "google_maps"
    CSV = "csv"
    MANUAL = "manual"


class LeadImportStatus(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class CampaignLeadStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    GENERATING = "generating"
    READY = "ready"
    APPROVED = "approved"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    BOUNCED = "bounced"
    UNSUBSCRIBED = "unsubscribed"
    SKIPPED = "skipped"


class EmailMessageStatus(StrEnum):
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    BOUNCED = "bounced"
    UNSUBSCRIBED = "unsubscribed"


class SuppressionReason(StrEnum):
    USER_UNSUBSCRIBED = "user_unsubscribed"
    BOUNCE = "bounce"
    INVALID = "invalid"
    MANUAL = "manual"


class EmailAccountStatus(StrEnum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    EXPIRED = "expired"
    ERROR = "error"


class WebsiteAnalysisStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


TEMPLATE_VARIABLES = [
    "business_name",
    "first_name",
    "city",
    "country",
    "website",
    "platform",
    "services",
    "website_score",
    "website_analysis",
    "website_issue",
    "recommended_service",
    "personalized_message",
    "sender_name",
    "sender_company",
    "unsubscribe_url",
]

KNOWN_PLATFORMS = [
    "shopify",
    "wordpress",
    "woocommerce",
    "wix",
    "webflow",
    "squarespace",
    "showit",
    "bigcommerce",
    "magento",
    "drupal",
    "ghost",
    "custom",
    "unknown",
]
