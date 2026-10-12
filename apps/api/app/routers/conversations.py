from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.services import conversations as service

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


@router.get("")
def list_conversations(
    unread: bool = False,
    lead_id: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Conversations where the lead replied, newest activity first."""
    return service.list_threads(db, user.id, unread_only=unread, lead_id=lead_id)


@router.get("/leads")
def replied_leads(
    q: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every lead who replied to your emails, latest reply first."""
    return service.replied_leads(db, user.id, q)


@router.get("/followups")
def lead_followups(lead_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Follow-up status of each first email sent to this lead (sent so far, next one due, or stopped)."""
    from app.models import EmailMessage
    from app.services.followups import followup_summary

    roots = (
        db.query(EmailMessage)
        .filter(
            EmailMessage.user_id == user.id,
            EmailMessage.lead_id == lead_id,
            EmailMessage.followup_of_id.is_(None),
            EmailMessage.status == "sent",
            EmailMessage.is_test.is_(False),
        )
        .order_by(EmailMessage.sent_at.desc())
        .all()
    )
    return [{"root_id": r.id, "subject": r.subject, "sent_at": r.sent_at, **followup_summary(db, r)} for r in roots]


@router.post("/followups/stop")
def stop_lead_followups(lead_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services.followups import stop_followups

    return {"stopped": stop_followups(db, user.id, lead_id)}


@router.get("/unread-count")
def unread_count(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"unread": service.unread_count(db, user.id)}


@router.get("/{root_id}")
def get_conversation(root_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.get_thread(db, user.id, root_id)


@router.post("/{root_id}/read")
def mark_read(root_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service.mark_read(db, user.id, root_id)
    return {"ok": True}


@router.post("/{root_id}/reply")
def reply(root_id: str, payload: ReplyIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Answer from the app: sent from your mailbox as a real reply in the same thread, with open tracking."""
    return service.send_reply(db, user.id, root_id, payload.body)
