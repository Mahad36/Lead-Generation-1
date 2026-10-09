from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import AuditLog, EmailMessage, Lead, SuppressionEntry, User
from app.services.suppression import add_suppression, delete_suppression, list_suppressions
from app.services.tracking import tracking_summary
from app.utils.normalize import normalize_email

router_suppression = APIRouter(prefix="/suppression", tags=["suppression"])
router_logs = APIRouter(prefix="/email-logs", tags=["email-logs"])
router_audit = APIRouter(prefix="/audit-logs", tags=["audit"])
router_public = APIRouter(tags=["public"])


class SuppressionIn(BaseModel):
    email: EmailStr
    reason: str = "manual"
    notes: str = ""


@router_suppression.get("")
def get_suppression(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return list_suppressions(db, user.id)


@router_suppression.post("")
def create_suppression(
    payload: SuppressionIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return add_suppression(db, user.id, payload.email, payload.reason, "manual", payload.notes)


@router_suppression.delete("/{entry_id}")
def remove_suppression(entry_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    delete_suppression(db, user.id, entry_id)
    return {"message": "Removed"}


@router_logs.get("")
def email_logs(
    page: int = 1,
    page_size: int = 25,
    status: str | None = None,
    campaign_id: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(EmailMessage).filter(EmailMessage.user_id == user.id)
    if status:
        query = query.filter(EmailMessage.status == status)
    if campaign_id:
        query = query.filter(EmailMessage.campaign_id == campaign_id)
    total = query.count()
    items = (
        query.order_by(EmailMessage.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    out = []
    for item in items:
        lead = db.get(Lead, item.lead_id) if item.lead_id else None
        out.append(
            {
                "id": item.id,
                "recipient": item.recipient_email,
                "business": lead.business_name if lead else item.recipient_name,
                "campaign_id": item.campaign_id,
                "subject": item.subject,
                "status": item.status,
                "sent_at": item.sent_at,
                "error": item.error_message,
                "account_id": item.email_account_id,
                "is_test": item.is_test,
                **tracking_summary(item),
            }
        )
    return {"items": out, "total": total, "page": page, "page_size": page_size}


@router_audit.get("")
def audit_logs(user: User = Depends(get_current_user), db: Session = Depends(get_db), page: int = 1, page_size: int = 50):
    query = db.query(AuditLog).filter(AuditLog.user_id == user.id)
    total = query.count()
    items = query.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router_public.get("/unsubscribe")
@router_public.post("/unsubscribe")
def unsubscribe(token: str = "", db: Session = Depends(get_db)):
    from app.utils.security import try_decode_token

    payload = try_decode_token(token)
    if not payload or payload.get("type") != "unsubscribe":
        raise HTTPException(status_code=400, detail="Invalid or expired unsubscribe link")
    value = normalize_email(payload.get("email") or "")
    user_id = payload.get("sub") or ""
    if not value or not user_id:
        raise HTTPException(status_code=400, detail="Invalid unsubscribe link")
    add_suppression(db, user_id, value, reason="user_unsubscribed", source="unsubscribe_link")
    return {"message": "You have been unsubscribed."}
