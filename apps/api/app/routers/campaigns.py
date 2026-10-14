from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_client_meta, get_current_user
from app.models import CampaignLead, Lead, User
from app.schemas.campaigns import (
    CampaignCreate,
    CampaignLeadEdit,
    CampaignLeadIds,
    CampaignLeadOut,
    CampaignOut,
    CampaignPreview,
)
from app.services import campaigns as campaign_service

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.get("", response_model=list[CampaignOut])
def list_campaigns(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models import Campaign

    campaigns = (
        db.query(Campaign).filter(Campaign.user_id == user.id).order_by(Campaign.created_at.desc()).all()
    )
    return [campaign_service.serialize_campaign(db, c) for c in campaigns]


@router.post("/preview", response_model=CampaignPreview)
def preview(payload: CampaignCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    lead_ids = campaign_service.resolve_campaign_lead_ids(db, user.id, payload)
    preview = campaign_service.campaign_preview(db, user.id, lead_ids, payload.daily_limit)
    preview["summary"] = campaign_service.launch_summary(db, user.id, lead_ids, payload)
    return preview


@router.post("", response_model=CampaignOut)
def create_campaign(
    payload: CampaignCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = campaign_service.create_campaign(db, user.id, payload, get_client_meta(request))
    if payload.send_now:
        from app.services.scraping_jobs import enqueue_task
        from workers.tasks.campaigns import launch_campaign, process_campaign_queue

        if campaign.status == "scheduled":
            enqueue_task(process_campaign_queue, campaign.id)
        else:
            enqueue_task(launch_campaign, campaign.id)
    return campaign_service.serialize_campaign(db, campaign)


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_campaign(campaign_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    campaign = campaign_service.get_campaign(db, user.id, campaign_id)
    return campaign_service.serialize_campaign(db, campaign)


class CampaignAccountIn(BaseModel):
    email_account_id: str | None = None  # the account for "single"
    mode: str | None = None  # single | auto | selected | health (None: single when an account is given, else auto)
    account_ids: list[str] = []  # for "selected"


@router.post("/{campaign_id}/email-account", response_model=CampaignOut)
def change_email_account(
    campaign_id: str,
    payload: CampaignAccountIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send the rest of this campaign from another of your email accounts."""
    campaign = campaign_service.set_campaign_email_account(
        db, user.id, campaign_id, payload.email_account_id, payload.mode, payload.account_ids
    )
    return campaign_service.serialize_campaign(db, campaign)


@router.get("/{campaign_id}/accounts")
def campaign_accounts(campaign_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Which account sent what: attempted, sent, failed (by reason), pending; per account and overall."""
    return campaign_service.campaign_account_report(db, user.id, campaign_id)


@router.get("/{campaign_id}/leads", response_model=list[CampaignLeadOut])
def campaign_leads(campaign_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    campaign = campaign_service.get_campaign(db, user.id, campaign_id)
    items = (
        db.query(CampaignLead)
        .filter(CampaignLead.campaign_id == campaign.id)
        .order_by(CampaignLead.created_at)
        .all()
    )
    from app.services.lead_outreach import outreach_for_leads

    leads = {lead.id: lead for lead in db.query(Lead).filter(Lead.id.in_([i.lead_id for i in items])).all()} if items else {}
    outreach = outreach_for_leads(db, user.id, list(leads.values()))
    out = []
    for item in items:
        lead = leads.get(item.lead_id)
        out.append(
            {
                "id": item.id,
                "lead_id": item.lead_id,
                "status": item.status,
                "subject": item.subject,
                "html_body": item.html_body,
                "text_body": item.text_body,
                "personalized_message": item.personalized_message,
                "approved": item.approved,
                "skip_reason": item.skip_reason,
                "error_message": item.error_message,
                "sent_at": item.sent_at,
                "business_name": lead.business_name if lead else None,
                "recipient_email": lead.primary_email if lead else None,
                "contact_name": lead.contact_name if lead else "",
                "website_url": lead.website_url if lead else "",
                "city": lead.city if lead else "",
                "outreach": outreach.get(item.lead_id),
            }
        )
    return out


@router.post("/{campaign_id}/generate")
def generate(campaign_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    campaign = campaign_service.get_campaign(db, user.id, campaign_id)
    from app.services.scraping_jobs import enqueue_task
    from workers.tasks.campaigns import generate_campaign_emails

    enqueue_task(generate_campaign_emails, campaign.id)
    return {"message": "Email generation queued"}


@router.post("/{campaign_id}/leads/{campaign_lead_id}/approve")
def approve_one(
    campaign_id: str,
    campaign_lead_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign_service.get_campaign(db, user.id, campaign_id)
    item = db.get(CampaignLead, campaign_lead_id)
    if not item or item.campaign_id != campaign_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Campaign lead not found")
    item.approved = True
    item.status = "approved"
    db.commit()
    return {"message": "Approved"}


@router.post("/{campaign_id}/approve")
def approve_bulk(
    campaign_id: str,
    payload: CampaignLeadIds,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign_service.get_campaign(db, user.id, campaign_id)
    items = db.query(CampaignLead).filter(CampaignLead.id.in_(payload.ids), CampaignLead.campaign_id == campaign_id)
    count = 0
    for item in items:
        item.approved = True
        item.status = "approved"
        count += 1
    db.commit()
    return {"approved": count}


@router.post("/{campaign_id}/leads/{campaign_lead_id}/skip")
def skip_one(
    campaign_id: str,
    campaign_lead_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign_service.get_campaign(db, user.id, campaign_id)
    item = db.get(CampaignLead, campaign_lead_id)
    if item:
        item.status = "skipped"
        item.skip_reason = "manual"
        db.commit()
    return {"message": "Skipped"}


@router.patch("/{campaign_id}/leads/{campaign_lead_id}")
def edit_one(
    campaign_id: str,
    campaign_lead_id: str,
    payload: CampaignLeadEdit,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign_service.get_campaign(db, user.id, campaign_id)
    item = db.get(CampaignLead, campaign_lead_id)
    if not item:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.commit()
    return {"message": "Updated"}


@router.post("/{campaign_id}/send", response_model=CampaignOut)
def send_campaign(
    campaign_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign_service.get_campaign(db, user.id, campaign_id)
    from app.services.scraping_jobs import enqueue_task
    from workers.tasks.campaigns import launch_campaign

    enqueue_task(launch_campaign, campaign_id)
    campaign = campaign_service.get_campaign(db, user.id, campaign_id)
    return campaign_service.serialize_campaign(db, campaign)


@router.post("/{campaign_id}/start", response_model=CampaignOut)
def start(
    campaign_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = campaign_service.start_campaign(db, user.id, campaign_id, get_client_meta(request))
    from app.services.scraping_jobs import enqueue_task
    from workers.tasks.campaigns import process_campaign_queue

    enqueue_task(process_campaign_queue, campaign.id)
    return campaign_service.serialize_campaign(db, campaign)


@router.post("/{campaign_id}/pause", response_model=CampaignOut)
def pause(
    campaign_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = campaign_service.pause_campaign(db, user.id, campaign_id, "manual", get_client_meta(request))
    return campaign_service.serialize_campaign(db, campaign)


@router.post("/{campaign_id}/resume", response_model=CampaignOut)
def resume(
    campaign_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = campaign_service.start_campaign(db, user.id, campaign_id, get_client_meta(request))
    from app.services.scraping_jobs import enqueue_task
    from workers.tasks.campaigns import process_campaign_queue

    enqueue_task(process_campaign_queue, campaign.id)
    return campaign_service.serialize_campaign(db, campaign)


@router.post("/{campaign_id}/stop", response_model=CampaignOut)
def stop(
    campaign_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = campaign_service.stop_campaign(db, user.id, campaign_id, get_client_meta(request))
    return campaign_service.serialize_campaign(db, campaign)


@router.post("/{campaign_id}/duplicate", response_model=CampaignOut)
def duplicate(campaign_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    campaign = campaign_service.duplicate_campaign(db, user.id, campaign_id)
    return campaign_service.serialize_campaign(db, campaign)
