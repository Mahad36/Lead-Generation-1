from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_client_meta, get_current_user
from app.models import User
from app.schemas.scraping import OutreachAction, ScrapingJobCreate, ScrapingJobOut
from app.services import scraping_jobs as scraping_service

router = APIRouter(prefix="/scraping/jobs", tags=["scraping"])


@router.get("", response_model=list[ScrapingJobOut])
def list_jobs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [scraping_service.serialize_job(db, job) for job in scraping_service.list_jobs(db, user.id)]


@router.post("", response_model=ScrapingJobOut)
def create_job(
    payload: ScrapingJobCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return scraping_service.serialize_job(db, scraping_service.create_job(db, user.id, payload))


@router.get("/{job_id}", response_model=ScrapingJobOut)
def get_job(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return scraping_service.serialize_job(db, scraping_service.get_job(db, user.id, job_id))


@router.post("/{job_id}/start", response_model=ScrapingJobOut)
def start_job(
    job_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = scraping_service.set_job_status(db, user.id, job_id, "queued", get_client_meta(request))
    # Outreach for scraped leads is the personalised audit pipeline (services/auto_audits.py), not a template campaign.
    job = scraping_service.dispatch_scrape_job(db, job)
    return scraping_service.serialize_job(db, scraping_service.get_job(db, user.id, job.id))


@router.post("/{job_id}/pause", response_model=ScrapingJobOut)
def pause_job(
    job_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return scraping_service.serialize_job(
        db, scraping_service.set_job_status(db, user.id, job_id, "paused", get_client_meta(request))
    )


@router.post("/{job_id}/resume", response_model=ScrapingJobOut)
def resume_job(
    job_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = scraping_service.set_job_status(db, user.id, job_id, "running", get_client_meta(request))
    job = scraping_service.dispatch_scrape_job(db, job)
    return scraping_service.serialize_job(db, job)


@router.post("/{job_id}/cancel", response_model=ScrapingJobOut)
def cancel_job(
    job_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return scraping_service.serialize_job(
        db, scraping_service.set_job_status(db, user.id, job_id, "cancelled", get_client_meta(request))
    )


@router.delete("/{job_id}", status_code=204)
def delete_job(
    job_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scraping_service.delete_job(db, user.id, job_id, get_client_meta(request))


@router.post("/{job_id}/find-emails")
def find_missing_emails(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Re-check the websites of this scrape's leads that have no email yet (runs in the background)."""
    from app.services.email_recheck import leads_missing_email, start_recheck

    job = scraping_service.get_job(db, user.id, job_id)
    count = len(leads_missing_email(db, job))
    started = start_recheck(job.id, discard_missing=True) if count else False  # still none: removed
    return {"checking": count, "started": started}


@router.post("/{job_id}/outreach", response_model=ScrapingJobOut)
def file_outreach(
    job_id: str,
    payload: OutreachAction,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start / pause / resume / stop this file's automatic outreach, or change its send mode / daily limit."""
    from app.services.auto_audits import set_job_outreach

    job = scraping_service.get_job(db, user.id, job_id)
    set_job_outreach(
        db,
        job,
        action=payload.action,
        auto_send=payload.auto_send,
        daily_limit=payload.daily_limit,
        email_account_id=payload.email_account_id if "email_account_id" in payload.model_fields_set else ...,
    )
    return scraping_service.serialize_job(db, scraping_service.get_job(db, user.id, job_id))
