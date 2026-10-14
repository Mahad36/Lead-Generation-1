from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, update
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.logging_config import get_logger
from app.models import (
    Campaign,
    CampaignLead,
    EmailAccount,
    EmailMessage,
    EmailTemplate,
    Lead,
    UserSettings,
)
from app.schemas.campaigns import CampaignCreate
from app.services.ai import AINotConfiguredError, generate_personalized_email
from app.services.audit import write_audit
from app.services.deliverability import MIN_SEND_GAP_SECONDS, address_problem, pick_sending_account, sent_recently
from app.services.gmail import GmailSendError, daily_limit_reached, send_gmail_message
from app.services.limits import LimitValidationError, validate_send_limits
from app.services.suppression import is_suppressed
from app.services.templates import build_template_context, text_context
from app.services.gmail_signature import account_signature
from app.utils.signature import insert_signature
from app.utils.templates import render_template

logger = get_logger("campaigns")


def _settings_or_defaults(db: Session, user_id: str) -> UserSettings | None:
    return db.query(UserSettings).filter(UserSettings.user_id == user_id).first()


def _campaign_file_extra(db: Session, user_id: str, data: CampaignCreate) -> dict:
    if data.import_batch_id:
        from app.services.leads import get_import

        batch = get_import(db, user_id, data.import_batch_id)
        return {"file_kind": "import", "file_id": batch.id, "file_name": batch.filename}
    if data.scraping_job_id:
        from app.models import ScrapingJob
        from app.services.leads import scrape_file_name

        job = db.query(ScrapingJob).filter(ScrapingJob.id == data.scraping_job_id, ScrapingJob.user_id == user_id).first()
        if job:
            return {"file_kind": "scrape", "file_id": job.id, "file_name": scrape_file_name(job)}
    if data.use_manual:
        return {"file_kind": "manual", "file_id": "manual", "file_name": "Manual leads"}
    if data.use_all_ready:
        return {"file_kind": "all", "file_id": "", "file_name": "All leads"}
    return {"file_kind": "manual", "file_id": "manual", "file_name": "Selected leads"}


def resolve_campaign_lead_ids(db: Session, user_id: str, data: CampaignCreate) -> list[str]:
    if data.import_batch_id:
        from app.services.leads import get_import, import_ready_lead_ids

        batch = get_import(db, user_id, data.import_batch_id)
        if batch.status != "ready":
            raise HTTPException(
                status_code=400,
                detail="This file is still being analyzed. Wait until it is ready, then send.",
            )
        ids = import_ready_lead_ids(db, user_id, batch.id)
        if not ids:
            raise HTTPException(status_code=400, detail="No ready leads in this imported file")
        return ids
    if data.scraping_job_id:
        from app.models import ScrapingJob
        from app.services.leads import scrape_ready_lead_ids

        job = db.query(ScrapingJob).filter(ScrapingJob.id == data.scraping_job_id, ScrapingJob.user_id == user_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Scrape file not found")
        return scrape_ready_lead_ids(db, user_id, job.id)
    if data.use_manual:
        rows = (
            db.query(Lead.id)
            .filter(
                Lead.user_id == user_id,
                Lead.source == "manual",
                Lead.import_batch_id.is_(None),
                Lead.scraping_job_id.is_(None),
                Lead.primary_email != "",
            )
            .all()
        )
        ids = [row[0] for row in rows]
        if not ids:
            raise HTTPException(status_code=400, detail="No manual leads with an email yet")
        return ids
    if data.use_all_ready:
        rows = (
            db.query(Lead.id)
            .filter(Lead.user_id == user_id, Lead.primary_email != "", Lead.primary_email.isnot(None))
            .all()
        )
        ids = [row[0] for row in rows]
        if not ids:
            raise HTTPException(status_code=400, detail="No leads with an email address yet")
        return ids
    return list(dict.fromkeys(data.lead_ids))


def resolve_email_account(
    db: Session, user_id: str, account_id: str | None, *, require_connected: bool = True
) -> EmailAccount:
    query = db.query(EmailAccount).filter(EmailAccount.user_id == user_id)
    if account_id:
        account = query.filter(EmailAccount.id == account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="Email account not found")
        if require_connected and account.status != "connected":
            raise HTTPException(status_code=400, detail="Email account is not connected. Reconnect Gmail or SMTP first.")
        return account
    account = (
        query.filter(EmailAccount.status == "connected")
        .order_by(EmailAccount.is_default.desc(), EmailAccount.created_at.desc())
        .first()
    )
    if not account and not require_connected:
        account = query.order_by(EmailAccount.is_default.desc(), EmailAccount.created_at.desc()).first()
    if not account:
        raise HTTPException(status_code=400, detail="Connect Gmail first, then send the campaign")
    return account


def _campaign_html(body: str) -> str:
    text = (body or "").strip()
    if "<" in text and ">" in text:
        html = text
    else:
        parts = [f"<p>{line}</p>" for line in text.splitlines() if line.strip()]
        html = "".join(parts) or f"<p>{text}</p>"
    if "{{unsubscribe_url}}" not in html:
        html += '<p style="font-size:12px;color:#666"><a href="{{unsubscribe_url}}">Unsubscribe</a></p>'
    return html


def resolve_template(db: Session, user_id: str, data: CampaignCreate):
    from app.schemas.templates import TemplateCreate
    from app.services.templates import create_template, ensure_default_template, get_template

    subject = (data.subject or "").strip()
    body = (data.html_body or "").strip()
    if subject and body:
        return create_template(
            db,
            user_id,
            TemplateCreate(
                name=f"{data.name} email",
                subject=subject[:500],
                html_body=_campaign_html(body),
                text_body=(data.text_body or body).strip(),
                is_active=True,
            ),
        )
    if data.template_id:
        return get_template(db, user_id, data.template_id)
    return ensure_default_template(db, user_id)


def campaign_preview(
    db: Session,
    user_id: str,
    lead_ids: list[str],
    daily_limit: int | None = None,
) -> dict:
    settings = _settings_or_defaults(db, user_id)
    leads = db.query(Lead).filter(Lead.user_id == user_id, Lead.id.in_(lead_ids)).all()
    with_email = suppressed = contacted = dnc = 0
    will_send = 0
    skipped = []
    for lead in leads:
        if lead.do_not_contact:
            dnc += 1
            skipped.append({"lead_id": lead.id, "reason": "do_not_contact"})
            continue
        if not lead.primary_email:
            skipped.append({"lead_id": lead.id, "reason": "no_email"})
            continue
        with_email += 1
        if settings and settings.respect_suppression_list and is_suppressed(db, user_id, lead.primary_email):
            suppressed += 1
            skipped.append({"lead_id": lead.id, "reason": "suppressed"})
            continue
        if settings and settings.require_verified_emails and lead.email_verification_status != "valid":
            skipped.append({"lead_id": lead.id, "reason": "not_verified"})
            continue
        if settings and settings.skip_previously_contacted and lead.last_contacted_at:
            contacted += 1
            skipped.append({"lead_id": lead.id, "reason": "previously_contacted"})
            continue
        will_send += 1
    limit = daily_limit or (settings.default_daily_send_limit if settings else 50)
    min_delay = settings.default_min_send_delay_seconds if settings else 60
    estimated = int((will_send * ((min_delay + (settings.default_max_send_delay_seconds if settings else 180)) / 2)) / 60) if will_send else 0
    return {
        "selected_leads": len(leads),
        "with_email": with_email,
        "suppressed": suppressed,
        "previously_contacted": contacted,
        "do_not_contact": dnc,
        "will_send": will_send,
        "daily_limit": limit,
        "estimated_duration_minutes": estimated,
        "skipped_details": skipped,
    }


def create_campaign(db: Session, user_id: str, data: CampaignCreate, meta: dict) -> Campaign:
    settings = _settings_or_defaults(db, user_id) or UserSettings(user_id=user_id)
    app = get_settings()
    account = resolve_email_account(db, user_id, data.email_account_id, require_connected=bool(data.send_now))
    audit_mode = data.outreach_mode == "audit"
    # Audit campaigns have no template: every email is written by Claude from that lead's own website audit.
    template = None if audit_mode else resolve_template(db, user_id, data)
    send_now = bool(data.send_now)
    try:
        daily = data.daily_limit or settings.default_daily_send_limit or app.default_daily_send_limit
        daily, hourly, min_d, max_d = validate_send_limits(
            daily,
            daily,
            data.min_delay_seconds or settings.default_min_send_delay_seconds or app.default_min_send_delay_seconds,
            data.max_delay_seconds or settings.default_max_send_delay_seconds or app.default_max_send_delay_seconds,
        )
    except LimitValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if data.require_approval is not None:
        require_approval = data.require_approval
    elif send_now:
        require_approval = False
    else:
        require_approval = settings.require_approval_before_sending
    campaign = Campaign(
        user_id=user_id,
        name=data.name,
        email_account_id=account.id,
        template_id=template.id if template else None,
        daily_limit=daily,
        hourly_limit=hourly,
        min_delay_seconds=min_d,
        max_delay_seconds=max_d,
        start_date=data.start_date,
        end_date=data.end_date,
        require_approval=require_approval,
        skip_previously_contacted=data.skip_previously_contacted
        if data.skip_previously_contacted is not None
        else settings.skip_previously_contacted,
        status="draft",
    )
    db.add(campaign)
    db.flush()
    lead_ids = resolve_campaign_lead_ids(db, user_id, data)
    leads = (
        db.query(Lead)
        .filter(Lead.user_id == user_id, Lead.id.in_(lead_ids), Lead.primary_email != "")
        .all()
        if lead_ids
        else []
    )
    if not leads and not data.scraping_job_id:
        raise HTTPException(status_code=400, detail="No leads with an email address to campaign")
    for lead in leads:
        db.add(CampaignLead(campaign_id=campaign.id, lead_id=lead.id, status="pending"))
    extra = {
        **(campaign.extra or {}),
        **_campaign_file_extra(db, user_id, data),
        "outreach_mode": data.outreach_mode,
        "add_signature": data.add_signature,
        "followups": data.followups,
        # No inbox chosen: each email goes from the connected inbox with the most room today
        "rotate": not data.email_account_id,
        "schedule": data.schedule.as_dict() if data.schedule else None,
        # Template campaigns send in one batch: their schedule follows the leads' most common time zone
        "timezone": _main_timezone(leads),
    }
    if data.scheduled_at:
        extra["scheduled_at"] = data.scheduled_at.isoformat()
        campaign.start_date = data.scheduled_at.date()
        if data.send_now:
            campaign.status = "scheduled"
    campaign.extra = extra
    write_audit(db, user_id=user_id, action="campaign.created", entity_type="campaign", entity_id=campaign.id, **meta)
    db.commit()
    db.refresh(campaign)
    return campaign


def find_scrape_campaign(db: Session, user_id: str, job_id: str) -> Campaign | None:
    return (
        db.query(Campaign)
        .filter(
            Campaign.user_id == user_id,
            Campaign.extra.contains({"file_kind": "scrape", "file_id": job_id}),
        )
        .order_by(Campaign.created_at.desc())
        .first()
    )


def campaign_was_stopped(campaign: Campaign) -> bool:
    return campaign.error_message == "Stopped by user" or campaign.paused_reason == "stopped"


def start_auto_campaign_for_scrape(db: Session, user_id: str, job) -> Campaign | None:
    extra = job.extra or {}
    if extra.get("auto_send") is False:
        return None
    existing = find_scrape_campaign(db, user_id, job.id)
    if existing:
        return existing
    from app.services.leads import scrape_file_name

    try:
        campaign = create_campaign(
            db,
            user_id,
            CampaignCreate(
                name=scrape_file_name(job),
                scraping_job_id=job.id,
                send_now=True,
                require_approval=False,
                subject=extra.get("subject") or None,
                html_body=extra.get("html_body") or None,
            ),
            {},
        )
    except HTTPException as exc:
        if exc.status_code == 400 and "Connect Gmail" in str(exc.detail):
            logger.info("auto_campaign_skipped_no_gmail", job_id=job.id)
            return None
        raise
    campaign.extra = {**(campaign.extra or {}), "auto": True}
    campaign.status = "running"
    campaign.require_approval = False
    db.commit()
    from app.services.scraping_jobs import enqueue_task
    from workers.tasks.campaigns import process_campaign_queue

    enqueue_task(process_campaign_queue, campaign.id)
    return campaign


def attach_ready_lead_to_auto_campaign(db: Session, lead: Lead) -> CampaignLead | None:
    if not lead.scraping_job_id or not (lead.primary_email or "").strip() or lead.do_not_contact:
        return None
    campaign = find_scrape_campaign(db, lead.user_id, lead.scraping_job_id)
    if not campaign or not (campaign.extra or {}).get("auto") or campaign_was_stopped(campaign):
        return None
    exists = (
        db.query(CampaignLead)
        .filter(CampaignLead.campaign_id == campaign.id, CampaignLead.lead_id == lead.id)
        .first()
    )
    if exists:
        return exists
    item = CampaignLead(campaign_id=campaign.id, lead_id=lead.id, status="pending")
    db.add(item)
    if campaign.status in {"completed", "draft"}:
        campaign.status = "running"
        campaign.paused_reason = ""
    db.commit()
    if campaign.status == "running":
        from app.services.scraping_jobs import enqueue_task
        from workers.tasks.campaigns import process_campaign_queue

        enqueue_task(process_campaign_queue, campaign.id)
    return item


def get_campaign(db: Session, user_id: str, campaign_id: str) -> Campaign:
    campaign = (
        db.query(Campaign)
        .options(joinedload(Campaign.leads))
        .filter(Campaign.id == campaign_id, Campaign.user_id == user_id)
        .first()
    )
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


def serialize_campaign(db: Session, campaign: Campaign) -> dict:
    counts = dict(
        db.query(CampaignLead.status, func.count(CampaignLead.id))
        .filter(CampaignLead.campaign_id == campaign.id)
        .group_by(CampaignLead.status)
        .all()
    )
    return {
        "id": campaign.id,
        "name": campaign.name,
        "status": campaign.status,
        "email_account_id": campaign.email_account_id,
        "template_id": campaign.template_id,
        "daily_limit": campaign.daily_limit,
        "hourly_limit": campaign.hourly_limit,
        "min_delay_seconds": campaign.min_delay_seconds,
        "max_delay_seconds": campaign.max_delay_seconds,
        "start_date": campaign.start_date,
        "end_date": campaign.end_date,
        "require_approval": campaign.require_approval,
        "skip_previously_contacted": campaign.skip_previously_contacted,
        "sent_today": campaign.sent_today,
        "last_sent_at": campaign.last_sent_at,
        "next_eligible_at": campaign.next_eligible_at,
        "paused_reason": campaign.paused_reason,
        "error_message": campaign.error_message,
        "created_at": campaign.created_at,
        "lead_count": sum(counts.values()),
        "sent_count": counts.get("sent", 0),
        "failed_count": counts.get("failed", 0),
        "pending_count": counts.get("pending", 0) + counts.get("queued", 0) + counts.get("ready", 0) + counts.get("approved", 0),
        "file_kind": (campaign.extra or {}).get("file_kind", ""),
        "file_id": (campaign.extra or {}).get("file_id", ""),
        "file_name": (campaign.extra or {}).get("file_name", ""),
        "scheduled_at": (campaign.extra or {}).get("scheduled_at", "") or "",
        "outreach_mode": "audit" if is_audit_campaign(campaign) else "template",
        "auditing_count": counts.get("auditing", 0),
        "rotate": campaign_rotates(campaign),
        "followups": campaign_followups(campaign),
    }


def apply_daily_limit(db: Session, user_id: str, daily: int) -> None:
    for campaign in (
        db.query(Campaign)
        .filter(Campaign.user_id == user_id, Campaign.status.in_(["draft", "scheduled", "running", "paused"]))
        .all()
    ):
        campaign.daily_limit = daily
        campaign.hourly_limit = daily
    for account in db.query(EmailAccount).filter(EmailAccount.user_id == user_id).all():
        account.daily_limit = daily


def recover_stale_campaign_leads(db: Session, campaign_id: str) -> int:
    cutoff = datetime.now(UTC) - timedelta(seconds=90)
    sending = (
        db.query(CampaignLead)
        .filter(
            CampaignLead.campaign_id == campaign_id,
            CampaignLead.status == "sending",
            or_(CampaignLead.locked_at.is_(None), CampaignLead.locked_at < cutoff),
        )
        .all()
    )
    generating = (
        db.query(CampaignLead)
        .filter(CampaignLead.campaign_id == campaign_id, CampaignLead.status == "generating")
        .all()
    )
    for item in sending:
        item.status = "queued"
        item.locked_at = None
        item.locked_by = ""
    for item in generating:
        item.status = "pending"
        item.locked_at = None
        item.locked_by = ""
    if sending or generating:
        db.commit()
    return len(sending) + len(generating)


def campaign_has_work(db: Session, campaign_id: str) -> bool:
    return bool(
        db.query(CampaignLead)
        .filter(
            CampaignLead.campaign_id == campaign_id,
            CampaignLead.status.in_(["pending", "generating", "queued", "approved", "ready", "sending", "auditing"]),
        )
        .first()
    )


def reset_counters_if_needed(campaign: Campaign) -> None:
    from app.utils.dates import local_today

    today = local_today()  # daily limits reset at the user's midnight
    now = datetime.now(UTC)
    if campaign.sent_today_date != today:
        campaign.sent_today = 0
        campaign.sent_today_date = today
    hour_at = campaign.sent_this_hour_at
    if not hour_at or hour_at.hour != now.hour or hour_at.date() != now.date():
        campaign.sent_this_hour = 0
        campaign.sent_this_hour_at = now


def generate_for_campaign_lead(db: Session, campaign_lead_id: str) -> CampaignLead:
    cl = db.get(CampaignLead, campaign_lead_id)
    if not cl:
        raise HTTPException(status_code=404, detail="Campaign lead not found")
    campaign = db.get(Campaign, cl.campaign_id)
    if campaign and is_audit_campaign(campaign):
        return cl  # written by the audit pipeline when the lead's turn comes
    lead = db.get(Lead, cl.lead_id)
    template = db.get(EmailTemplate, campaign.template_id) if campaign else None
    if not campaign or not lead or not template:
        raise HTTPException(status_code=400, detail="Campaign is missing template or lead")
    cl.status = "generating"
    db.commit()
    context = build_template_context(db, campaign.user_id, lead)
    settings = _settings_or_defaults(db, campaign.user_id)
    analysis = {}
    if lead.website and lead.website.analysis:
        a = lead.website.analysis
        analysis = {
            "issues": a.issues,
            "opportunities": a.opportunities,
            "recommended_service": (a.recommended_services or [None])[0],
            "services": a.services,
            "business_summary": a.business_summary,
        }
    try:
        personalized = generate_personalized_email(
            db,
            campaign.user_id,
            template_html=template.html_body,
            business={
                "business_name": lead.business_name,
                "city": lead.city,
                "category": lead.category,
                "website": lead.website_url,
                "platform": lead.platform,
                "website_score": lead.website_score,
            },
            analysis=analysis,
            sender={
                "sender_name": settings.sender_name if settings else "",
                "company_name": settings.company_name if settings else "",
                "company_description": settings.company_description if settings else "",
                "services": [s.name for s in (settings.user.services if settings else [])]
                if settings and settings.user
                else [],
            },
        )
        context["personalized_message"] = personalized
        cl.personalized_message = personalized
        cl.html_body = render_template(template.html_body, {**context, "personalized_message": personalized})
        if "{{personalized_message}}" not in template.html_body:
            cl.html_body = f"{personalized}<br/><br/>{cl.html_body}"
    except Exception as exc:
        if not isinstance(exc, AINotConfiguredError):
            logger.warning("personalization_failed", lead_id=lead.id, error=str(exc)[:200])
        cl.html_body = render_template(template.html_body, context)
        cl.personalized_message = ""
    cl.subject = render_template(template.subject, context)
    cl.text_body = render_template(template.text_body or "", text_context(context, db, campaign.user_id)) or ""
    cl.status = "ready" if campaign.require_approval else "approved"
    if not campaign.require_approval:
        cl.approved = True
    db.commit()
    db.refresh(cl)
    return cl


def generate_pending_emails(db: Session, campaign_id: str) -> int:
    items = (
        db.query(CampaignLead)
        .filter(CampaignLead.campaign_id == campaign_id, CampaignLead.status.in_(["pending", "generating"]))
        .all()
    )
    for item in items:
        generate_for_campaign_lead(db, item.id)
    return len(items)


def approve_generated_leads(db: Session, campaign_id: str) -> int:
    items = (
        db.query(CampaignLead)
        .filter(CampaignLead.campaign_id == campaign_id, CampaignLead.status.in_(["ready", "approved", "queued"]))
        .all()
    )
    count = 0
    for item in items:
        item.approved = True
        if item.status == "ready":
            item.status = "approved"
        count += 1
    db.commit()
    return count


def launch_campaign(db: Session, user_id: str, campaign_id: str, meta: dict) -> Campaign:
    campaign = get_campaign(db, user_id, campaign_id)
    if not is_audit_campaign(campaign):
        campaign.require_approval = False
        db.commit()
        generate_pending_emails(db, campaign.id)
        approve_generated_leads(db, campaign.id)
    # Audit campaigns keep their choice: require_approval = audit automatically, review each email before sending.
    return start_campaign(db, user_id, campaign.id, meta)


def start_campaign(db: Session, user_id: str, campaign_id: str, meta: dict) -> Campaign:
    campaign = get_campaign(db, user_id, campaign_id)
    if campaign.status in {"completed", "stopped"}:
        raise HTTPException(status_code=400, detail="Campaign cannot be started again")
    account = db.get(EmailAccount, campaign.email_account_id) if campaign.email_account_id else None
    if campaign_rotates(campaign):
        account = pick_sending_account(db, user_id) or account
    if not account or account.status != "connected":
        raise HTTPException(status_code=400, detail="Reconnect Gmail or SMTP before starting this campaign")
    campaign.status = "running"
    campaign.paused_reason = ""
    campaign.error_message = ""
    write_audit(db, user_id=user_id, action="campaign.started", entity_type="campaign", entity_id=campaign.id, **meta)
    db.commit()
    db.refresh(campaign)
    return campaign


def pause_campaign(db: Session, user_id: str, campaign_id: str, reason: str, meta: dict) -> Campaign:
    campaign = get_campaign(db, user_id, campaign_id)
    campaign.status = "paused"
    campaign.paused_reason = reason
    write_audit(db, user_id=user_id, action="campaign.paused", entity_type="campaign", entity_id=campaign.id, details={"reason": reason}, **meta)
    db.commit()
    db.refresh(campaign)
    return campaign


def claim_next_campaign_lead(
    db: Session, campaign_id: str, worker_id: str, statuses: tuple[str, ...] = ("approved", "queued")
) -> CampaignLead | None:
    """Atomically claim one sendable lead so two workers cannot send the same email."""
    now = datetime.now(UTC)
    stmt = (
        update(CampaignLead)
        .where(
            CampaignLead.campaign_id == campaign_id,
            CampaignLead.status.in_(list(statuses)),
            CampaignLead.locked_at.is_(None),
        )
        .values(status="sending", locked_at=now, locked_by=worker_id)
        .returning(CampaignLead.id)
    )
    result = db.execute(stmt)
    row = result.first()
    db.commit()
    if not row:
        return None
    return db.get(CampaignLead, row[0])


def send_claimed_lead(db: Session, campaign_lead: CampaignLead) -> str:
    campaign = db.get(Campaign, campaign_lead.campaign_id)
    lead = db.get(Lead, campaign_lead.lead_id)
    account = db.get(EmailAccount, campaign.email_account_id) if campaign else None
    if campaign and campaign_rotates(campaign):
        account = pick_sending_account(db, campaign.user_id) or account
    if not campaign or not lead or not account:
        campaign_lead.status = "failed"
        campaign_lead.error_message = "Missing campaign, lead, or email account"
        db.commit()
        return "failed"

    reset_counters_if_needed(campaign)
    if campaign.sent_today >= campaign.daily_limit:
        campaign.status = "paused"
        campaign.paused_reason = "daily_limit_reached"
        campaign_lead.status = "queued"
        campaign_lead.locked_at = None
        campaign_lead.locked_by = ""
        db.commit()
        return "daily_limit"
    if campaign.sent_this_hour >= campaign.hourly_limit:
        campaign.next_eligible_at = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        campaign_lead.status = "queued"
        campaign_lead.locked_at = None
        campaign_lead.locked_by = ""
        db.commit()
        return "hourly_limit"

    settings = _settings_or_defaults(db, campaign.user_id)
    if lead.do_not_contact or not lead.primary_email:
        campaign_lead.status = "skipped"
        campaign_lead.skip_reason = "do_not_contact" if lead.do_not_contact else "no_email"
        campaign_lead.locked_at = None
        db.commit()
        return "skipped"
    if (not settings or settings.respect_suppression_list) and is_suppressed(db, campaign.user_id, lead.primary_email):
        campaign_lead.status = "unsubscribed"
        campaign_lead.skip_reason = "suppressed"
        campaign_lead.locked_at = None
        db.commit()
        return "suppressed"
    if campaign.skip_previously_contacted and lead.last_contacted_at:
        campaign_lead.status = "skipped"
        campaign_lead.skip_reason = "previously_contacted"
        campaign_lead.locked_at = None
        db.commit()
        return "skipped"
    if lead_has_audit_outreach(db, lead.id):
        # The personalised audit email is on its way (or sent); never send a template on top of it.
        campaign_lead.status = "skipped"
        campaign_lead.skip_reason = "audit_outreach"
        campaign_lead.locked_at = None
        db.commit()
        return "skipped"
    if account.user_id != campaign.user_id:
        campaign_lead.status = "failed"
        campaign_lead.error_message = "Email account does not belong to this user"
        campaign_lead.locked_at = None
        db.commit()
        return "failed"
    if account.status != "connected":
        campaign_lead.status = "failed"
        campaign_lead.error_message = "Email account is not connected"
        campaign_lead.locked_at = None
        db.commit()
        return "failed"
    if account.paused_reason:
        # Too many bounces: wait until the inbox is resumed in Email setup (or another inbox is chosen)
        campaign.status = "paused"
        campaign.paused_reason = "inbox_paused_bounces"
        campaign_lead.status = "queued"
        campaign_lead.locked_at = None
        campaign_lead.locked_by = ""
        db.commit()
        return "inbox_paused"
    if sent_recently(db, account):
        # Another email just left this inbox (audit, follow-up, other campaign): wait a little
        campaign.next_eligible_at = datetime.now(UTC) + timedelta(seconds=MIN_SEND_GAP_SECONDS)
        campaign_lead.status = "queued"
        campaign_lead.locked_at = None
        campaign_lead.locked_by = ""
        db.commit()
        return "spacing"
    problem = address_problem(db, campaign.user_id, lead.primary_email)
    if problem:
        campaign_lead.status = "skipped"
        campaign_lead.skip_reason = "email_domain_rejects_mail"
        campaign_lead.error_message = problem
        campaign_lead.locked_at = None
        db.commit()
        return "skipped"
    if daily_limit_reached(db, account):
        # The account's own daily limit covers every email it sends (audits, other campaigns), not just this one.
        campaign.status = "paused"
        campaign.paused_reason = "daily_limit_reached"
        campaign_lead.status = "queued"
        campaign_lead.locked_at = None
        campaign_lead.locked_by = ""
        db.commit()
        return "daily_limit"
    if not (campaign_lead.subject or "").strip() or not (campaign_lead.html_body or "").strip():
        campaign_lead.status = "failed"
        campaign_lead.error_message = "Email content is missing"
        campaign_lead.locked_at = None
        db.commit()
        return "failed"

    from app.services.tracking import (
        new_rfc_message_id,
        open_pixel_html,
        track_links,
        tracks_opens,
        unsubscribe_url,
    )
    from app.utils.templates import render_template as render_vars

    unsub_url = unsubscribe_url(campaign.user_id, lead.primary_email)
    rfc_message_id = new_rfc_message_id(account.email_address)
    html_body = render_vars(campaign_lead.html_body, {"unsubscribe_url": unsub_url})
    text_body = render_vars(campaign_lead.text_body or "", {"unsubscribe_url": unsub_url})
    if "unsubscribe" not in html_body.lower():
        html_body += f'<p style="font-size:12px;color:#666"><a href="{unsub_url}">Unsubscribe</a></p>'
    if (campaign.extra or {}).get("add_signature", True) is not False:
        # The sending Gmail's own signature (read from Gmail), above the Unsubscribe line
        sig_html, sig_text = account_signature(account)
        if sig_html:
            html_body = insert_signature(html_body, sig_html, settings.sender_name if settings else "",
                                         settings.company_name if settings else "")
            if text_body.strip() and sig_text:
                text_body = f"{text_body.rstrip()}\n\n{sig_text}"
    tracked = tracks_opens(db, campaign.user_id)  # adds the open-tracking image; the visible email is the same

    message = EmailMessage(
        user_id=campaign.user_id,
        campaign_id=campaign.id,
        campaign_lead_id=campaign_lead.id,
        lead_id=lead.id,
        email_account_id=account.id,
        recipient_email=lead.primary_email,
        recipient_name=lead.business_name,
        subject=campaign_lead.subject,
        html_body=html_body,
        text_body=text_body,
        status="sending",
        rfc_message_id=rfc_message_id,
        followups_enabled=bool(not settings or settings.followups_enabled) and campaign_followups(campaign),
    )
    db.add(message)
    db.commit()
    try:
        provider_id = send_gmail_message(
            db,
            account,
            to=lead.primary_email,
            subject=campaign_lead.subject,
            html_body=(
                track_links(html_body, message.id, skip=(unsub_url,)) + open_pixel_html(message.id) if tracked else html_body
            ),
            text_body=text_body,
            headers={"Message-ID": rfc_message_id},  # no bulk-mail headers: 1-to-1 email, not a list
        )
        now = datetime.now(UTC)
        message.status = "sent"
        message.provider_message_id = provider_id
        message.sent_at = now
        campaign_lead.status = "sent"
        campaign_lead.sent_at = now
        campaign_lead.locked_at = None
        campaign.sent_today += 1
        campaign.sent_this_hour += 1
        campaign.last_sent_at = now
        delay = random.randint(campaign.min_delay_seconds, campaign.max_delay_seconds)
        campaign.next_eligible_at = now + timedelta(seconds=delay)
        lead.last_contacted_at = now
        lead.email_status = "contacted"
        remaining = (
            db.query(func.count(CampaignLead.id))
            .filter(
                CampaignLead.campaign_id == campaign.id,
                CampaignLead.status.in_(["pending", "queued", "ready", "approved", "generating"]),
            )
            .scalar()
        )
        if remaining == 0:
            campaign.status = "completed"
        db.commit()
        return "sent"
    except GmailSendError as exc:
        message.status = "failed"
        message.error_message = str(exc)
        campaign_lead.attempts += 1
        campaign_lead.error_message = str(exc)
        campaign_lead.locked_at = None
        campaign_lead.locked_by = ""
        campaign_lead.status = "queued" if exc.retryable and campaign_lead.attempts < 3 else "failed"
        db.commit()
        return "failed"


def duplicate_campaign(db: Session, user_id: str, campaign_id: str) -> Campaign:
    original = get_campaign(db, user_id, campaign_id)
    clone = Campaign(
        user_id=user_id,
        name=f"{original.name} (copy)",
        email_account_id=original.email_account_id,
        template_id=original.template_id,
        status="draft",
        daily_limit=original.daily_limit,
        hourly_limit=original.hourly_limit,
        min_delay_seconds=original.min_delay_seconds,
        max_delay_seconds=original.max_delay_seconds,
        require_approval=original.require_approval,
        skip_previously_contacted=original.skip_previously_contacted,
    )
    db.add(clone)
    db.flush()
    for item in original.leads:
        db.add(CampaignLead(campaign_id=clone.id, lead_id=item.lead_id, status="pending"))
    db.commit()
    db.refresh(clone)
    return clone


# ---------------------------------------------------------------- audit campaigns
# An "audit" campaign sends no template: the queue below starts the website audit pipeline for one lead at a time
# (screenshots -> Claude CRO review -> PDF -> Claude email from that audit -> send with the PDF attached), honouring
# the campaign's daily limit, the gap between leads, pause/stop and scheduling. Each campaign lead is "auditing" until
# its audit ends, then mirrors the result (sent / ready for review / failed).

OPEN_CAMPAIGN_LEAD_STATUSES = ("pending", "generating", "ready", "approved", "queued", "sending", "auditing")
OPEN_CAMPAIGN_STATUSES = ("draft", "scheduled", "running", "paused")


def is_audit_campaign(campaign: Campaign) -> bool:
    return (campaign.extra or {}).get("outreach_mode") == "audit"


def lead_has_audit_outreach(db: Session, lead_id: str) -> bool:
    """True when a website audit for this lead is running or its email was sent."""
    from app.models import WebsiteAudit
    from app.services.website_audit import ACTIVE_STATUSES

    return bool(
        db.query(WebsiteAudit.id)
        .filter(
            WebsiteAudit.lead_id == lead_id,
            or_(WebsiteAudit.status.in_(ACTIVE_STATUSES), WebsiteAudit.sent_at.isnot(None)),
        )
        .first()
    )


def _mirror_audit(cl: CampaignLead, audit) -> None:
    from app.services.website_audit import ACTIVE_STATUSES

    from app.services.website_audit import WAITING_STEPS

    if audit.sent_at and audit.status in {"sent", "ready"}:
        cl.status, cl.sent_at, cl.error_message = "sent", audit.sent_at, ""
    elif audit.status in ACTIVE_STATUSES:
        cl.status = "auditing"
    elif audit.status == "ready" and audit.step in WAITING_STEPS:
        cl.status, cl.error_message = "auditing", audit.step
    elif audit.status == "ready" and audit.step == CAMPAIGN_PAUSED_STEP:
        cl.status, cl.error_message = "auditing", "Paused: the email is sent when the campaign resumes"
    elif audit.status == "ready":
        cl.status, cl.error_message = "ready", ""  # audit done, email waiting for review on the lead page
    elif audit.status in {"failed", "send_failed"}:
        cl.status, cl.error_message = "failed", audit.error_message or "The audit failed"
    elif audit.status == "cancelled":
        cl.status, cl.skip_reason = "skipped", "audit_stopped"
    cl.subject = audit.email_subject or cl.subject
    cl.locked_at, cl.locked_by = None, ""


def sync_campaign_lead_for_audit(db: Session, audit) -> None:
    """Called when an audit finishes or is sent: update the campaign lead that started it."""
    rows = (
        db.query(CampaignLead)
        .join(Campaign, Campaign.id == CampaignLead.campaign_id)
        .filter(
            CampaignLead.lead_id == audit.lead_id,
            Campaign.user_id == audit.user_id,
            CampaignLead.status.in_(["auditing", "ready", "failed"]),
        )
        .all()
    )
    changed = False
    for cl in rows:
        campaign = db.get(Campaign, cl.campaign_id)
        if campaign and is_audit_campaign(campaign):
            _mirror_audit(cl, audit)
            changed = True
    if changed:
        db.commit()


def _seconds_until_tomorrow() -> int:
    from app.utils.dates import seconds_until_local_midnight

    return seconds_until_local_midnight()


def advance_audit_campaign(db: Session, campaign: Campaign) -> tuple[str, int | None]:
    """Start the next lead's audit. Returns (status, seconds until the queue should run again; None = done)."""
    from app.models import WebsiteAudit
    from app.services.website_audit import ACTIVE_STATUSES, _expire_stale, start_audit

    release_paused_sends(db, campaign)  # emails held while the campaign was paused
    # Catch up on audits that finished (or whose worker died) without updating their campaign lead.
    for cl in db.query(CampaignLead).filter(CampaignLead.campaign_id == campaign.id, CampaignLead.status == "auditing"):
        audit = (
            db.query(WebsiteAudit)
            .filter(WebsiteAudit.lead_id == cl.lead_id)
            .order_by(WebsiteAudit.created_at.desc())
            .first()
        )
        if not audit:
            cl.status, cl.error_message = "failed", "The audit was not found"
            continue
        if audit.status in ACTIVE_STATUSES:
            _expire_stale(db, audit)
        _mirror_audit(cl, audit)
    db.commit()

    reset_counters_if_needed(campaign)
    account = db.get(EmailAccount, campaign.email_account_id) if campaign.email_account_id else None
    account_full = bool(account) and not campaign.require_approval and daily_limit_reached(db, account)
    if campaign.sent_today >= campaign.daily_limit or account_full:
        campaign.status, campaign.paused_reason = "paused", "daily_limit_reached"
        db.commit()
        return "daily_limit", _seconds_until_tomorrow()
    # Claude reviews are slow: share AUTO_AUDIT_BATCH_SIZE with the automatic poller.
    running = (
        db.query(func.count(WebsiteAudit.id))
        .filter(WebsiteAudit.user_id == campaign.user_id, WebsiteAudit.status.in_(ACTIVE_STATUSES))
        .scalar()
        or 0
    )
    if running >= max(1, get_settings().auto_audit_batch_size):
        return "busy", 30

    claimed = claim_next_campaign_lead(db, campaign.id, "audit-queue", ("pending", "approved", "queued"))
    if not claimed:
        waiting = (
            db.query(CampaignLead.id)
            .filter(CampaignLead.campaign_id == campaign.id, CampaignLead.status.in_(["auditing", "sending"]))
            .first()
        )
        if waiting:
            return "waiting_audits", 30
        campaign.status = "completed"
        db.commit()
        return "completed", None

    lead = db.get(Lead, claimed.lead_id)
    settings = _settings_or_defaults(db, campaign.user_id)
    reason = ""
    if not lead:
        reason = "missing_lead"
    elif lead.do_not_contact:
        reason = "do_not_contact"
    elif not (lead.primary_email or "").strip():
        reason = "no_email"
    elif not (lead.website_url or "").strip():
        reason = "no_website"
    elif (not settings or settings.respect_suppression_list) and is_suppressed(db, campaign.user_id, lead.primary_email):
        reason = "suppressed"
    elif campaign.skip_previously_contacted and lead.last_contacted_at:
        reason = "previously_contacted"
    elif address_problem(db, campaign.user_id, lead.primary_email):
        reason = "email_domain_rejects_mail"
    if reason:
        claimed.status = "unsubscribed" if reason == "suppressed" else "skipped"
        claimed.skip_reason, claimed.locked_at, claimed.locked_by = reason, None, ""
        db.commit()
        return "skipped", 0

    existing = (
        db.query(WebsiteAudit)
        .filter(WebsiteAudit.lead_id == lead.id, or_(WebsiteAudit.status.in_(ACTIVE_STATUSES), WebsiteAudit.sent_at.isnot(None)))
        .order_by(WebsiteAudit.created_at.desc())
        .first()
    )
    if existing:  # already being audited / emailed (e.g. by the automatic poller): follow it, don't start another
        _mirror_audit(claimed, existing)
        db.commit()
        return "linked", 0

    try:
        start_audit(
            db,
            campaign.user_id,
            lead.id,
            auto_send=not campaign.require_approval,
            email_account_id=None if campaign_rotates(campaign) else campaign.email_account_id,
            trigger="campaign",
        )
    except HTTPException as exc:
        db.rollback()
        claimed = db.get(CampaignLead, claimed.id)
        claimed.status, claimed.error_message = "failed", str(exc.detail)
        claimed.locked_at, claimed.locked_by = None, ""
        db.commit()
        return "failed", 0

    now = datetime.now(UTC)
    claimed.status, claimed.locked_at, claimed.locked_by = "auditing", None, ""
    claimed.attempts = (claimed.attempts or 0) + 1
    campaign.sent_today += 1  # counts leads processed today (each one is an audit + email)
    campaign.last_sent_at = now
    delay = random.randint(campaign.min_delay_seconds, max(campaign.min_delay_seconds, campaign.max_delay_seconds))
    campaign.next_eligible_at = now + timedelta(seconds=delay)
    db.commit()
    logger.info("campaign_audit_started", campaign_id=campaign.id, lead_id=lead.id)
    return "started", delay


# ---------------------------------------------------------------- pause / stop for audit campaigns

CAMPAIGN_PAUSED_STEP = "Waiting: the campaign is paused (sends when it resumes)"
CAMPAIGN_STOPPED_STEP = "Ready: the campaign was stopped, so this email was not sent"


def _main_timezone(leads: list[Lead]) -> str:
    from collections import Counter

    from app.services.deliverability import lead_timezone

    zones = Counter(tz for tz in (lead_timezone(lead) for lead in leads) if tz)
    return zones.most_common(1)[0][0] if zones else ""


def campaign_for_audit(db: Session, audit) -> Campaign | None:
    """The audit campaign this audit was started by (None for scrape-file and manual audits)."""
    if audit.trigger != "campaign":
        return None
    return (
        db.query(Campaign)
        .join(CampaignLead, CampaignLead.campaign_id == Campaign.id)
        .filter(
            Campaign.user_id == audit.user_id,
            CampaignLead.lead_id == audit.lead_id,
            Campaign.extra.contains({"outreach_mode": "audit"}),
        )
        .order_by(CampaignLead.updated_at.desc())
        .first()
    )


def campaign_adds_signature(db: Session, audit) -> bool:
    """False when the audit belongs to a campaign started with "Add my signature" switched off."""
    campaign = campaign_for_audit(db, audit)
    return not campaign or (campaign.extra or {}).get("add_signature", True) is not False


def campaign_rotates(campaign: Campaign) -> bool:
    """No inbox chosen: emails rotate across every connected inbox."""
    return bool((campaign.extra or {}).get("rotate"))


def campaign_followups(campaign: Campaign | None) -> bool:
    """The campaign's "Send follow-ups" switch (on unless switched off)."""
    return not campaign or (campaign.extra or {}).get("followups", True) is not False


def campaign_hold(db: Session, audit) -> str:
    """Return "paused" or "stopped" when the audit's campaign is paused or stopped, else an empty string."""
    if audit.trigger != "campaign":
        return ""
    row = (
        db.query(Campaign.status)
        .join(CampaignLead, CampaignLead.campaign_id == Campaign.id)
        .filter(
            Campaign.user_id == audit.user_id,
            CampaignLead.lead_id == audit.lead_id,
            or_(
                CampaignLead.status == "auditing",
                and_(CampaignLead.status == "skipped", CampaignLead.skip_reason == "campaign_stopped"),
            ),
            Campaign.extra.contains({"outreach_mode": "audit"}),
        )
        .order_by(CampaignLead.updated_at.desc())
        .first()
    )
    if not row:
        return ""
    return {"paused": "paused", "stopped": "stopped", "failed": "stopped"}.get(row[0], "")


def stop_campaign(db: Session, user_id: str, campaign_id: str, meta: dict) -> Campaign:
    """Stop for good: nothing more is sent. Queued leads are skipped; audits already running finish without sending."""
    from app.models import WebsiteAudit
    from app.services.website_audit import ACTIVE_STATUSES

    campaign = get_campaign(db, user_id, campaign_id)
    campaign.status, campaign.paused_reason, campaign.error_message = "stopped", "stopped", "Stopped by user"
    open_items = (
        db.query(CampaignLead)
        .filter(CampaignLead.campaign_id == campaign.id, CampaignLead.status.in_(OPEN_CAMPAIGN_LEAD_STATUSES))
        .all()
    )
    for item in open_items:
        if item.status == "auditing":
            for audit in (
                db.query(WebsiteAudit)
                .filter(
                    WebsiteAudit.lead_id == item.lead_id,
                    WebsiteAudit.sent_at.is_(None),
                    or_(WebsiteAudit.status.in_(ACTIVE_STATUSES), WebsiteAudit.status == "ready"),
                )
                .all()
            ):
                audit.auto_send = False
                if audit.status == "ready":
                    audit.step = CAMPAIGN_STOPPED_STEP
        item.status, item.skip_reason, item.locked_at, item.locked_by = "skipped", "campaign_stopped", None, ""
    write_audit(
        db,
        user_id=user_id,
        action="campaign.stopped",
        entity_type="campaign",
        entity_id=campaign.id,
        details={"cancelled_leads": len(open_items)},
        **meta,
    )
    db.commit()
    db.refresh(campaign)
    return campaign


def release_paused_sends(db: Session, campaign: Campaign) -> int:
    """After a resume: send the emails whose audits finished while the campaign was paused."""
    from app.models import WebsiteAudit
    from app.services.website_audit import WAITING_FOR_LIMIT, AuditSendError, send_audit

    lead_ids = [
        lead_id
        for (lead_id,) in db.query(CampaignLead.lead_id).filter(
            CampaignLead.campaign_id == campaign.id, CampaignLead.status == "auditing"
        )
    ]
    if not lead_ids:
        return 0
    held = (
        db.query(WebsiteAudit)
        .filter(
            WebsiteAudit.lead_id.in_(lead_ids),
            WebsiteAudit.status == "ready",
            WebsiteAudit.sent_at.is_(None),
            WebsiteAudit.step == CAMPAIGN_PAUSED_STEP,
        )
        .all()
    )
    sent = 0
    for audit in held:
        if not audit.auto_send:
            audit.step = "Ready to review and send"
            db.commit()
            continue
        try:
            send_audit(db, audit.user_id, audit.id)
            sent += 1
        except HTTPException as exc:
            db.rollback()
            if exc.status_code == 429:
                audit = db.get(WebsiteAudit, audit.id)
                from app.services.deliverability import waiting_step

                audit.step = waiting_step(exc, WAITING_FOR_LIMIT)  # the poller sends it when allowed
                db.commit()
                break
        except AuditSendError:
            pass  # recorded on the audit (send_failed)
    return sent


def set_campaign_email_account(db: Session, user_id: str, campaign_id: str, account_id: str | None) -> Campaign:
    """Send the rest of this campaign from another account ("" / None: the default account).

    Emails already sent stay as they are; audits still being prepared or waiting to send switch to the new account.
    """
    from app.models import WebsiteAudit

    campaign = get_campaign(db, user_id, campaign_id)
    account = resolve_email_account(db, user_id, account_id or None)
    campaign.email_account_id = account.id
    campaign.extra = {**(campaign.extra or {}), "rotate": not account_id}
    lead_ids = [row[0] for row in db.query(CampaignLead.lead_id).filter(CampaignLead.campaign_id == campaign.id)]
    if lead_ids and is_audit_campaign(campaign):
        for audit in db.query(WebsiteAudit).filter(
            WebsiteAudit.user_id == user_id,
            WebsiteAudit.trigger == "campaign",
            WebsiteAudit.lead_id.in_(lead_ids),
            WebsiteAudit.sent_at.is_(None),
        ):
            audit.email_account_id = None if not account_id else account.id  # None: rotation picks at send time
    db.commit()
    db.refresh(campaign)
    logger.info("campaign_account_changed", campaign_id=campaign.id, account_id=account.id)
    return campaign
