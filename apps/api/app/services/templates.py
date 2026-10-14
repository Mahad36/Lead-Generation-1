from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import EmailTemplate, Lead, UserSettings, WebsiteAnalysis
from app.schemas.templates import TemplateCreate, TemplateUpdate
from app.utils.templates import render_template, validate_template_variables


DEFAULT_TEMPLATE_NAME = "Starter outreach"
DEFAULT_TEMPLATE_SUBJECT = "Quick idea for {{business_name}}"
DEFAULT_TEMPLATE_HTML = """<p>Hi {{business_name}},</p>
<p>I was looking at your {{category}} business in {{city}} and wanted to reach out.</p>
<p>{{personalized_message}}</p>
<p>Would you be open to a short conversation this week?</p>
<p>{{sender_name}}<br/>{{sender_company}}</p>
<p style="font-size:12px;color:#666"><a href="{{unsubscribe_url}}">Unsubscribe</a></p>
"""
DEFAULT_TEMPLATE_TEXT = """Hi {{business_name}},

I was looking at your {{category}} business in {{city}} and wanted to reach out.

{{personalized_message}}

Would you be open to a short conversation this week?

{{sender_name}}
{{sender_company}}

Unsubscribe: {{unsubscribe_url}}
"""


def ensure_default_template(db: Session, user_id: str) -> EmailTemplate:
    existing = (
        db.query(EmailTemplate)
        .filter(EmailTemplate.user_id == user_id)
        .order_by(EmailTemplate.created_at.asc())
        .first()
    )
    if existing:
        return existing
    template = EmailTemplate(
        user_id=user_id,
        name=DEFAULT_TEMPLATE_NAME,
        subject=DEFAULT_TEMPLATE_SUBJECT,
        html_body=DEFAULT_TEMPLATE_HTML,
        text_body=DEFAULT_TEMPLATE_TEXT,
        is_active=True,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


def list_templates(db: Session, user_id: str) -> list[EmailTemplate]:
    ensure_default_template(db, user_id)
    return (
        db.query(EmailTemplate)
        .filter(EmailTemplate.user_id == user_id)
        .order_by(EmailTemplate.created_at.desc())
        .all()
    )


def get_template(db: Session, user_id: str, template_id: str) -> EmailTemplate:
    template = (
        db.query(EmailTemplate)
        .filter(EmailTemplate.id == template_id, EmailTemplate.user_id == user_id)
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


def create_template(db: Session, user_id: str, data: TemplateCreate) -> EmailTemplate:
    unknown = validate_template_variables(data.subject, data.html_body, data.text_body)
    template = EmailTemplate(user_id=user_id, **data.model_dump())
    db.add(template)
    db.commit()
    db.refresh(template)
    template.unknown_variables = unknown  # type: ignore[attr-defined]
    return template


def update_template(db: Session, user_id: str, template_id: str, data: TemplateUpdate) -> EmailTemplate:
    template = get_template(db, user_id, template_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(template, key, value)
    db.commit()
    db.refresh(template)
    return template


def delete_template(db: Session, user_id: str, template_id: str) -> None:
    template = get_template(db, user_id, template_id)
    db.delete(template)
    db.commit()


def text_context(context: dict, db: Session, user_id: str) -> dict:
    """The same values for a plain-text body."""
    return dict(context)


def build_template_context(db: Session, user_id: str, lead: Lead | None) -> dict:
    settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    analysis = None
    if lead and lead.website and lead.website.analysis:
        analysis = lead.website.analysis
    elif lead and lead.website:
        analysis = db.query(WebsiteAnalysis).filter(WebsiteAnalysis.website_id == lead.website.id).first()
    first_name = ""
    if lead and lead.business_name:
        first_name = lead.business_name.split()[0]
    issues = (analysis.issues if analysis else []) or []
    recs = (analysis.recommended_services if analysis else []) or []
    services = (analysis.services if analysis else []) or []
    return {
        "business_name": lead.business_name if lead else "ABC Plumbing",
        "first_name": first_name or "there",
        "city": lead.city if lead else "Miami",
        "country": lead.country if lead else "USA",
        "website": lead.website_url if lead else "https://example-business.test",
        "category": lead.category if lead else "plumbing",
        "platform": lead.platform if lead else "Wix",
        "services": ", ".join(services) if services else (lead.category if lead else "plumbing"),
        "website_score": lead.website_score if lead and lead.website_score is not None else "",
        "lead_score": lead.lead_score if lead and lead.lead_score is not None else "",
        "website_analysis": analysis.summary if analysis else "",
        "website_issue": issues[0]["title"] if issues else "",
        "recommended_service": recs[0] if recs else "",
        "personalized_message": "",
        "sender_name": settings.sender_name if settings else "",
        "sender_company": settings.company_name if settings else "",
        "sender_website": settings.company_website if settings else "",
        # The sending Gmail's own signature is added when the email is sent; {{signature}} in old templates is empty
        "signature": "",
        "unsubscribe_url": "{{unsubscribe_url}}",
    }


def preview_template(
    db: Session,
    user_id: str,
    template: EmailTemplate | None,
    lead: Lead | None,
    subject: str | None = None,
    html_body: str | None = None,
    text_body: str | None = None,
) -> dict:
    context = build_template_context(db, user_id, lead)
    subject_s = subject if subject is not None else (template.subject if template else "")
    html_s = html_body if html_body is not None else (template.html_body if template else "")
    text_s = text_body if text_body is not None else (template.text_body if template else "")
    return {
        "subject": render_template(subject_s, context),
        "html_body": render_template(html_s, context),
        "text_body": render_template(text_s, text_context(context, db, user_id)),
        "context": context,
    }
