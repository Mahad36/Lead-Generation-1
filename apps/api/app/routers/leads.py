from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_client_meta, get_current_user
from app.models import User
from app.schemas.leads import (
    ImportSummary,
    LeadCreate,
    LeadDetail,
    LeadIdsRequest,
    LeadFileOut,
    LeadImportOut,
    LeadListItem,
    LeadListResponse,
    LeadUpdate,
)
from app.services import leads as lead_service
from app.services.lead_outreach import outreach_for_leads

router = APIRouter(prefix="/leads", tags=["leads"])


@router.get("", response_model=LeadListResponse)
def list_leads(
    q: str | None = None,
    country: str | None = None,
    city: str | None = None,
    category: str | None = None,
    platform: str | None = None,
    has_email: str | None = None,
    has_website: str | None = None,
    email_verification_status: str | None = None,
    email_status: str | None = None,
    status: str | None = None,
    source: str | None = None,
    import_batch_id: str | None = None,
    scraping_job_id: str | None = None,
    min_lead_score: int | None = None,
    max_lead_score: int | None = None,
    min_website_score: int | None = None,
    max_website_score: int | None = None,
    sort: str = "created_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = 25,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    params = locals()
    params.pop("user")
    params.pop("db")
    items, total = lead_service.list_leads(db, user.id, params)
    outreach = outreach_for_leads(db, user.id, items)
    return {
        "items": [
            LeadListItem.model_validate(lead).model_copy(
                update=lead_service.lead_file_info(db, lead) | {"outreach": outreach.get(lead.id)}
            )
            for lead in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("", response_model=LeadDetail)
def create_lead(
    payload: LeadCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lead = lead_service.create_lead(db, user.id, payload, get_client_meta(request))
    return lead_service.lead_detail_payload(db, lead_service.get_lead(db, user.id, lead.id))


@router.get("/export")
def export_leads(
    ids: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    q: str | None = None,
    country: str | None = None,
    city: str | None = None,
    category: str | None = None,
    platform: str | None = None,
    has_email: str | None = None,
):
    id_list = [i for i in (ids or "").split(",") if i]
    csv_data = lead_service.export_leads_csv(
        db,
        user.id,
        {
            "q": q,
            "country": country,
            "city": city,
            "category": category,
            "platform": platform,
            "has_email": has_email,
        },
        id_list or None,
    )
    return PlainTextResponse(csv_data, media_type="text/csv")


@router.post("/import", response_model=ImportSummary)
async def import_leads(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    filename = file.filename or "leads.csv"
    return lead_service.import_leads_file(db, user.id, raw, get_client_meta(request), filename=filename)


@router.get("/files", response_model=list[LeadFileOut])
def list_lead_files(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return lead_service.list_lead_files(db, user.id)


@router.get("/imports", response_model=list[LeadImportOut])
def list_lead_imports(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [lead_service.serialize_import(item) for item in lead_service.list_imports(db, user.id)]


@router.get("/imports/{import_id}", response_model=LeadImportOut)
def get_lead_import(import_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return lead_service.serialize_import(lead_service.get_import(db, user.id, import_id))


@router.delete("/imports/{import_id}", status_code=204)
def delete_lead_import(
    import_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lead_service.delete_import(db, user.id, import_id, get_client_meta(request))


@router.post("/bulk-delete")
def bulk_delete(
    payload: LeadIdsRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    count = lead_service.delete_leads(db, user.id, payload.ids, get_client_meta(request))
    return {"deleted": count}


@router.delete("/{lead_id}")
def delete_lead(
    lead_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lead_service.get_lead(db, user.id, lead_id)
    count = lead_service.delete_leads(db, user.id, [lead_id], get_client_meta(request))
    return {"deleted": count}


@router.get("/{lead_id}")
def get_lead(lead_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    lead = lead_service.get_lead(db, user.id, lead_id)
    return lead_service.lead_detail_payload(db, lead)


@router.patch("/{lead_id}")
def update_lead(
    lead_id: str,
    payload: LeadUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lead = lead_service.update_lead(db, user.id, lead_id, payload)
    return lead_service.lead_detail_payload(db, lead_service.get_lead(db, user.id, lead.id))


@router.post("/{lead_id}/analyze")
def analyze_lead(lead_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    lead = lead_service.get_lead(db, user.id, lead_id)
    from app.services.lead_pipeline import enqueue_lead_website_processing

    enqueue_lead_website_processing([lead.id])
    return {"message": "Website analysis queued", "lead_id": lead.id}


@router.post("/{lead_id}/do-not-contact")
def mark_dnc(lead_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    lead = lead_service.update_lead(db, user.id, lead_id, LeadUpdate(do_not_contact=True))
    if lead.primary_email:
        from app.services.suppression import add_suppression

        add_suppression(db, user.id, lead.primary_email, reason="manual", source="do_not_contact")
    return {"message": "Lead marked do not contact"}
