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
