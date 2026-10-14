from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_client_meta, get_current_user
from app.models import EmailAccount, Lead, User
from app.schemas.audits import AuditDraftUpdate, AuditSendRequest, AuditStart, QuickAuditRequest
from app.services import website_audit as audit_service

router = APIRouter(tags=["website-audits"])


@router.post("/audits/quick", status_code=202)
def quick_audit(
    payload: QuickAuditRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create (or reuse) a lead from website + customer data, then start the audit."""
    meta = get_client_meta(request)
    lead = audit_service.lead_for_quick_audit(db, user.id, payload, meta)
    audit = audit_service.start_audit(
        db,
        user.id,
        lead.id,
        recipient_email=payload.email,
        auto_send=payload.auto_send,
        email_account_id=payload.email_account_id or None,
        website_url=payload.website_url,
        meta=meta,
    )
    return audit_service.serialize_audit(audit)


@router.post("/leads/{lead_id}/audits", status_code=202)
def start_audit(
    lead_id: str,
    payload: AuditStart,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    audit = audit_service.start_audit(
        db,
        user.id,
        lead_id,
        recipient_email=payload.recipient_email,
        auto_send=payload.auto_send,
        email_account_id=payload.email_account_id,
        meta=get_client_meta(request),
    )
    return audit_service.serialize_audit(audit)


@router.get("/audits")
def list_audits(
    lead_id: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    platform: str | None = Query(default=None, max_length=64),
    finding_type: str | None = Query(default=None, max_length=32),
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Audits, newest first. With `page`: {items, total, page, page_size} (Website audits page); else the latest 50."""
    offset = (page - 1) * page_size if page else 0
    audits = audit_service.list_audits(
        db, user.id, lead_id, q=q, limit=page_size if page else 50, offset=offset, platform=platform, finding_type=finding_type
    )
    for audit in audits:
        audit_service._expire_stale(db, audit)
    # Platform from the audit's own detection, else the lead's (from the website analysis)
    lead_ids = {a.lead_id for a in audits if a.lead_id}
    platforms = (
        dict(db.query(Lead.id, Lead.platform).filter(Lead.id.in_(lead_ids)).all()) if lead_ids else {}
    )
    # The inbox each email went from (rotation picks it when sending; set on the audit then)
    account_ids = {a.email_account_id for a in audits if a.email_account_id and a.sent_at}
    senders = (
        dict(db.query(EmailAccount.id, EmailAccount.email_address).filter(EmailAccount.id.in_(account_ids)).all())
        if account_ids
        else {}
    )
    items = []
    for a in audits:
        data = audit_service.serialize_audit(a, full=False)
        data["platform"] = data.get("platform") or platforms.get(a.lead_id) or ""
        data["sent_from"] = senders.get(a.email_account_id, "") if a.sent_at else ""
        items.append(data)
    if not page:
        return items
    total = audit_service.count_audits(db, user.id, lead_id, q=q, platform=platform, finding_type=finding_type)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/audits/platforms")
def audit_platforms(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Platforms among your audits, with counts (for the filter)."""
    return audit_service.audit_platforms(db, user.id)


class AngleIn(BaseModel):
    text: str = Field(default="", max_length=1500)


@router.post("/audits/{audit_id}/outreach-angle")
def save_outreach_angle(audit_id: str, payload: AngleIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Your edited outreach angle: used when the email is (re)drafted."""
    return audit_service.serialize_audit(audit_service.set_outreach_angle(db, user.id, audit_id, payload.text))


@router.post("/audits/reprocess-platform")
def reprocess_platform(payload: dict | None = None, user: User = Depends(get_current_user)):
    """Add platform intelligence to older audits in the background, a few at a time (findings are kept)."""
    from app.services.platform_reprocess import start

    body = payload or {}
    return start(user.id, limit=int(body.get("limit") or 25), with_ai=bool(body.get("with_ai", True)))


@router.get("/audits/reprocess-platform")
def reprocess_status(user: User = Depends(get_current_user)):
    from app.services.platform_reprocess import status

    return status(user.id)


@router.get("/audits/auto-status")
def auto_audit_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services.auto_audits import status_for_user

    return status_for_user(db, user.id)


@router.get("/audits/{audit_id}")
def get_audit(audit_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    audit = audit_service.get_audit(db, user.id, audit_id)
    audit_service._expire_stale(db, audit)
    return audit_service.serialize_audit(audit)


@router.get("/audits/{audit_id}/pdf")
def download_pdf(
    audit_id: str,
    download: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    content, filename = audit_service.get_pdf(db, user.id, audit_id)
    disposition = "attachment" if download else "inline"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@router.patch("/audits/{audit_id}")
def update_draft(
    audit_id: str,
    payload: AuditDraftUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    audit = audit_service.update_draft(db, user.id, audit_id, **payload.model_dump())
    return audit_service.serialize_audit(audit)


@router.post("/audits/{audit_id}/send")
def send_audit(
    audit_id: str,
    payload: AuditSendRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        audit = audit_service.send_audit(
            db, user.id, audit_id, **payload.model_dump(), meta=get_client_meta(request), manual=True
        )  # you pressed Send: no sending-hours wait
    except audit_service.AuditSendError as exc:
        raise HTTPException(status_code=502, detail=f"Email was not sent: {exc}") from exc
    return audit_service.serialize_audit(audit)


@router.post("/audits/{audit_id}/cancel")
def cancel_audit(
    audit_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    audit = audit_service.cancel_audit(db, user.id, audit_id, meta=get_client_meta(request))
    return audit_service.serialize_audit(audit)


@router.post("/audits/{audit_id}/regenerate-pdf")
def regenerate_pdf(
    audit_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    audit = audit_service.regenerate_pdf(db, user.id, audit_id, meta=get_client_meta(request))
    return audit_service.serialize_audit(audit)


@router.post("/audits/{audit_id}/regenerate-email")
def regenerate_email(
    audit_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    audit = audit_service.start_email_redraft(db, user.id, audit_id, meta=get_client_meta(request))
    return audit_service.serialize_audit(audit)
