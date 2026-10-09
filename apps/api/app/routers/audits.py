from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_client_meta, get_current_user
from app.models import User
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
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    audits = audit_service.list_audits(db, user.id, lead_id, q=q)
    for audit in audits:
        audit_service._expire_stale(db, audit)
    return [audit_service.serialize_audit(a, full=False) for a in audits]


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
        audit = audit_service.send_audit(db, user.id, audit_id, **payload.model_dump(), meta=get_client_meta(request))
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
