"""Per-lead outreach workflow status, derived from the lead, its latest website audit and the email sent for it.

Nothing here is stored: the stage is computed from the records the pipeline already writes, so it can't drift out of
sync. Scraped -> Website found -> Auditing -> Audited -> PDF generated -> Email generated -> Sent -> Opened ->
Link clicked -> Replied, with Failed / Send failed / Bounced / No website / No email as side states.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import EmailMessage, Lead, WebsiteAudit
from app.services.tracking import tracking_summary

# Order of the happy path; the UI draws these as a stepper.
STAGES = [
    ("scraped", "Scraped"),
    ("website_found", "Website found"),
    ("auditing", "Auditing website"),
    ("audited", "Website audited"),
    ("pdf_generated", "PDF generated"),
    ("email_generated", "Email generated"),
    ("sent", "Email sent"),
    ("opened", "Email opened"),
    ("clicked", "Link clicked"),
    ("replied", "Replied"),
]
SIDE_STAGES = {
    "no_website": "No website",
    "no_email": "No email",
    "failed": "Failed",
    "send_failed": "Sending failed",
    "sending": "Sending",
    "bounced": "Bounced",
    "do_not_contact": "Do not contact",
    "audit_stopped": "Audit stopped",
}
LABELS = dict(STAGES) | SIDE_STAGES
AUDITING = {"queued", "fetching", "measuring", "capturing", "analyzing"}


def stage_for(lead: Lead, audit: WebsiteAudit | None, message: EmailMessage | None) -> str:
    if audit and audit.sent_at and message:
        if message.bounced_at:
            return "bounced"
        if message.replied_at:
            return "replied"
        if message.click_count:
            return "clicked"
        if message.open_count:
            return "opened"
        return "sent"
    if audit:
        status = audit.status
        if status in AUDITING:
            return "auditing"
        if status == "generating_pdf":
            return "audited"
        if status == "drafting_email":
            return "pdf_generated" if audit.pdf_size else "audited"
        if status == "ready":
            return "email_generated"
        if status in {"sending", "send_failed", "failed"}:
            return status
        if status == "cancelled":
            return "audit_stopped"
    if lead.do_not_contact:
        return "do_not_contact"
    if not (lead.website_url or "").strip():
        return "no_website"
    if not (lead.primary_email or "").strip():
        return "no_email"
    return "website_found"


def _latest_audits(db: Session, lead_ids: list[str]) -> dict[str, WebsiteAudit]:
    latest: dict[str, WebsiteAudit] = {}
    if not lead_ids:
        return latest
    rows = (
        db.query(WebsiteAudit)
        .filter(WebsiteAudit.lead_id.in_(lead_ids))
        .order_by(WebsiteAudit.created_at.desc())
        .all()
    )
    for audit in rows:
        latest.setdefault(audit.lead_id, audit)
    return latest


def _attempts(db: Session, lead_ids: list[str]) -> dict[str, int]:
    from sqlalchemy import func

    if not lead_ids:
        return {}
    rows = (
        db.query(WebsiteAudit.lead_id, func.count(WebsiteAudit.id))
        .filter(WebsiteAudit.lead_id.in_(lead_ids))
        .group_by(WebsiteAudit.lead_id)
        .all()
    )
    return dict(rows)


def _open_campaigns(db: Session, user_id: str, lead_ids: list[str]) -> dict[str, str]:
    """{lead_id: campaign name} for leads waiting in an open campaign."""
    from app.models import Campaign, CampaignLead
    from app.services.campaigns import OPEN_CAMPAIGN_LEAD_STATUSES, OPEN_CAMPAIGN_STATUSES

    if not lead_ids:
        return {}
    rows = (
        db.query(CampaignLead.lead_id, Campaign.name)
        .join(Campaign, Campaign.id == CampaignLead.campaign_id)
        .filter(
            Campaign.user_id == user_id,
            Campaign.status.in_(OPEN_CAMPAIGN_STATUSES),
            CampaignLead.status.in_(OPEN_CAMPAIGN_LEAD_STATUSES),
            CampaignLead.lead_id.in_(lead_ids),
        )
        .all()
    )
    return dict(rows)


def outreach_for_leads(db: Session, user_id: str, leads: list[Lead]) -> dict[str, dict]:
    """{lead_id: outreach summary} for a page of leads, with a fixed number of queries."""
    ids = [lead.id for lead in leads]
    audits = _latest_audits(db, ids)
    attempts = _attempts(db, ids)
    message_ids = [a.email_message_id for a in audits.values() if a.email_message_id]
    messages = (
        {m.id: m for m in db.query(EmailMessage).filter(EmailMessage.id.in_(message_ids)).all()} if message_ids else {}
    )
    from app.models import ScrapingJob

    job_ids = {lead.scraping_job_id for lead in leads if lead.scraping_job_id}
    file_outreach = (
        dict(db.query(ScrapingJob.id, ScrapingJob.outreach_status).filter(ScrapingJob.id.in_(job_ids)).all())
        if job_ids
        else {}
    )
    queued_in = _open_campaigns(db, user_id, ids)
    out: dict[str, dict] = {}
    for lead in leads:
        audit = audits.get(lead.id)
        message = messages.get(audit.email_message_id) if audit and audit.email_message_id else None
        stage = stage_for(lead, audit, message)
        note = ""
        if audit and audit.status == "ready" and not audit.sent_at and audit.auto_send and "daily send limit" in (audit.step or ""):
            note = "Waiting for the daily send limit; it goes out automatically"
        elif audit and audit.status == "ready" and not audit.sent_at and "paused" in (audit.step or ""):
            note = "Paused: the email is sent when outreach resumes"
        if stage == "website_found":
            if lead.id in queued_in:
                note = f"Queued in campaign “{queued_in[lead.id]}”"
            else:
                note = {
                    "running": "Queued for automatic outreach",
                    "paused": "Outreach for this file is paused",
                    "stopped": "Outreach for this file was stopped",
                }.get(file_outreach.get(lead.scraping_job_id, ""), "Outreach is off for this file")
        out[lead.id] = {
            "stage": stage,
            "stage_label": LABELS.get(stage, stage),
            "note": note,
            "audit_id": audit.id if audit else None,
            "audit_status": audit.status if audit else "",
            "audit_step": audit.step if audit else "",
            "failed_step": audit.failed_step if audit else "",
            "error": audit.error_message if audit and audit.status in {"failed", "send_failed"} else "",
            "attempts": attempts.get(lead.id, 0),
            "score": audit.overall_score if audit else None,
            "has_pdf": bool(audit and audit.pdf_size),
            "pdf_filename": audit.pdf_filename if audit else "",
            "has_email": bool(audit and audit.email_html),
            "email_subject": audit.email_subject if audit else "",
            "sent_at": audit.sent_at if audit else None,
            **tracking_summary(message),
        }
    return out


def outreach_for_lead(db: Session, lead: Lead) -> dict:
    return outreach_for_leads(db, lead.user_id, [lead])[lead.id]
