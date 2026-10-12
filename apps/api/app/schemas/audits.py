from pydantic import BaseModel, Field


class AuditStart(BaseModel):
    recipient_email: str | None = None
    auto_send: bool = False
    email_account_id: str | None = None


class QuickAuditRequest(BaseModel):
    business_name: str = Field(min_length=1, max_length=500)
    website_url: str = Field(min_length=3, max_length=2000)
    email: str = Field(min_length=3, max_length=320)
    city: str = ""
    country: str = ""
    category: str = ""
    auto_send: bool = False
    email_account_id: str | None = None  # send from this account (none: the default account)


class AuditDraftUpdate(BaseModel):
    email_subject: str | None = Field(default=None, max_length=500)
    email_html: str | None = Field(default=None, max_length=20000)
    recipient_email: str | None = None


class AuditSendRequest(AuditDraftUpdate):
    email_account_id: str | None = None
