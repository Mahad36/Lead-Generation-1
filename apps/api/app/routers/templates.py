from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Lead, User
from app.schemas.templates import (
    TemplateCreate,
    TemplateOut,
    TemplatePreviewOut,
    TemplatePreviewRequest,
    TemplateUpdate,
)
from app.services import templates as template_service
from app.utils.templates import validate_template_variables

router = APIRouter(tags=["templates"])


def _out(template) -> dict:
    unknown = validate_template_variables(template.subject, template.html_body, template.text_body)
    return {**{c.name: getattr(template, c.name) for c in template.__table__.columns}, "unknown_variables": unknown}


@router.get("", response_model=list[TemplateOut])
def list_templates(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [_out(t) for t in template_service.list_templates(db, user.id)]


@router.post("", response_model=TemplateOut)
def create_template(
    payload: TemplateCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _out(template_service.create_template(db, user.id, payload))


@router.get("/{template_id}", response_model=TemplateOut)
def get_template(template_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _out(template_service.get_template(db, user.id, template_id))


@router.put("/{template_id}", response_model=TemplateOut)
@router.patch("/{template_id}", response_model=TemplateOut)
def update_template(
    template_id: str,
    payload: TemplateUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _out(template_service.update_template(db, user.id, template_id, payload))


@router.delete("/{template_id}")
def delete_template(template_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    template_service.delete_template(db, user.id, template_id)
    return {"message": "Deleted"}


@router.post("/{template_id}/preview", response_model=TemplatePreviewOut)
def preview_template(
    template_id: str,
    payload: TemplatePreviewRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    template = template_service.get_template(db, user.id, template_id)
    lead = None
    if payload.lead_id:
        lead = db.query(Lead).filter(Lead.id == payload.lead_id, Lead.user_id == user.id).first()
    return template_service.preview_template(
        db, user.id, template, lead, payload.subject, payload.html_body, payload.text_body
    )
