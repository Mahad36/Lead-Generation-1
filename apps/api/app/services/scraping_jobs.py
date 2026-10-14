from datetime import UTC, datetime
import threading

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.logging_config import get_logger
from app.models import ScrapingJob, ScrapingSearch, UserSettings
from app.schemas.scraping import ScrapingJobCreate
from app.services.audit import write_audit
from app.services.duplicates import find_duplicate_lead
from app.services.leads import persist_lead
from app.services.apify_maps import APIFY_TOKEN_MISSING, ApifyError, scrape_google_maps_apify
from app.services.integrations import resolve_apify_token, resolve_scraper_provider
from app.services.scraper import (
    ScrapedBusiness,
    ScrapingBlockedError,
    ScrapingCancelledError,
    build_maps_query,
    scrape_google_maps,
    short_browser_error,
)

logger = get_logger("scraping_jobs")


def _refresh_job(db: Session, job: ScrapingJob) -> bool:
    from sqlalchemy.orm.exc import ObjectDeletedError

    try:
        db.refresh(job)
        return True
    except ObjectDeletedError:
        return False


def create_job(db: Session, user_id: str, data: ScrapingJobCreate) -> ScrapingJob:
    settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    max_results = data.max_results_per_search or (settings.scrape_max_results if settings else get_settings().scrape_max_results)
    name = data.name
    if not name or name in {"Scraping job", "Maps scrape"}:
        name = " + ".join(f"{item.city} {item.category}".strip() for item in data.searches)[:255] or "Maps scrape"
    from app.services.auto_audits import own_email_account_id

    job = ScrapingJob(
        user_id=user_id,
        name=name,
        status="draft",
        max_results_per_search=max_results,
        extra={
            "auto_send": data.auto_send,
            "subject": (data.subject or "").strip(),
            "html_body": (data.html_body or "").strip(),
            "followups": data.followups,
            "schedule": data.schedule.as_dict() if data.schedule else None,
            "recipients": {"include_generic": data.include_generic, "cc_enabled": data.cc_enabled, "cc_max": data.cc_max},
        },
        outreach_status="off" if data.outreach_mode == "off" else "running",
        outreach_auto_send=data.outreach_mode != "review",
        outreach_daily_limit=data.outreach_daily_limit,
        outreach_email_account_id=own_email_account_id(db, user_id, data.outreach_email_account_id),
    )
    db.add(job)
    db.flush()
    for index, search in enumerate(data.searches):
        db.add(
            ScrapingSearch(
                job_id=job.id,
                country=search.country,
                city=search.city,
                category=search.category,
                query=build_maps_query(search.country, search.city, search.category),
                sort_order=index,
            )
        )
    db.commit()
    db.refresh(job)
    return get_job(db, user_id, job.id)


def get_job(db: Session, user_id: str, job_id: str) -> ScrapingJob:
    job = (
        db.query(ScrapingJob)
        .options(joinedload(ScrapingJob.searches))
        .filter(ScrapingJob.id == job_id, ScrapingJob.user_id == user_id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Scraping job not found")
    return job


def job_outreach_status(db: Session, job: ScrapingJob) -> dict:
    from app.services.auto_audits import job_outreach_status as status

    return status(db, job)


def _finding_emails(job_id: str) -> bool:
    from app.services.email_recheck import is_running

    return is_running(job_id)


def serialize_job(db: Session, job: ScrapingJob) -> dict:
    from app.models import Lead
    from app.services.campaigns import find_scrape_campaign
    from sqlalchemy import func

    campaign = find_scrape_campaign(db, job.user_id, job.id)
    analyzed = (
        db.query(func.count(Lead.id))
        .filter(Lead.scraping_job_id == job.id, Lead.platform != "", Lead.platform.isnot(None))
        .scalar()
        or 0
    )
    return {
        "id": job.id,
        "name": job.name,
        "status": job.status,
        "max_results_per_search": job.max_results_per_search,
        "businesses_found": job.businesses_found,
        "new_leads": job.new_leads,
        "duplicate_leads": job.duplicate_leads,
        "errors_count": job.errors_count,
        "current_search_id": job.current_search_id,
        "current_query": job.current_query,
        "worker_status": job.worker_status,
        "error_message": job.error_message,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "searches": job.searches,
        "campaign_id": campaign.id if campaign else None,
        "campaign_status": campaign.status if campaign else "",
        "campaign_sent_today": campaign.sent_today if campaign else 0,
        "campaign_daily_limit": campaign.daily_limit if campaign else 0,
        "skipped_no_email": int((job.extra or {}).get("skipped_no_email") or 0),
        # Not kept: no email and no website, or no email even after searching the website
        "discarded": int((job.extra or {}).get("discarded_no_contact") or 0) + int((job.extra or {}).get("discarded_no_email") or 0),
        "analyzed_count": analyzed,
        "finding_emails": _finding_emails(job.id),
        "outreach": job_outreach_status(db, job),
    }


def list_jobs(db: Session, user_id: str) -> list[ScrapingJob]:
    return (
        db.query(ScrapingJob)
        .options(joinedload(ScrapingJob.searches))
        .filter(ScrapingJob.user_id == user_id)
        .order_by(ScrapingJob.created_at.desc())
        .all()
    )


def set_job_status(db: Session, user_id: str, job_id: str, status: str, meta: dict | None = None) -> ScrapingJob:
    job = get_job(db, user_id, job_id)
    job.status = status
    if status == "running" and not job.started_at:
        job.started_at = datetime.now(UTC)
        job.worker_status = "starting"
    if status in {"cancelled", "paused"}:
        job.worker_status = status
    if status == "cancelled":
        job.finished_at = datetime.now(UTC)
    if meta:
        write_audit(
            db,
            user_id=user_id,
            action=f"scraping.{status}",
            entity_type="scraping_job",
            entity_id=job.id,
            **meta,
        )
    db.commit()
    db.refresh(job)
    return job


def delete_job(db: Session, user_id: str, job_id: str, meta: dict | None = None) -> None:
    from app.models import Lead
    from app.services.campaigns import find_scrape_campaign

    job = get_job(db, user_id, job_id)
    job.status = "cancelled"
    job.worker_status = "cancelled"
    job.finished_at = datetime.now(UTC)
    campaign = find_scrape_campaign(db, user_id, job.id)
    if campaign and campaign.status in {"draft", "running", "paused"}:
        campaign.status = "failed"
        campaign.error_message = "File deleted"
        campaign.paused_reason = "stopped"
        db.add(campaign)
    db.query(Lead).filter(Lead.user_id == user_id, Lead.scraping_job_id == job.id).delete(synchronize_session=False)
    db.delete(job)
    write_audit(
        db,
        user_id=user_id,
        action="scraping.deleted",
        entity_type="scraping_job",
        entity_id=job_id,
        **(meta or {}),
    )
    db.commit()


def broker_available() -> bool:
    try:
        import redis

        client = redis.Redis.from_url(get_settings().celery_broker_url, socket_connect_timeout=1)
        client.ping()
        return True
    except Exception:
        return False


def _run_job_inline(job_id: str) -> None:
    from app.database import get_session

    db = get_session()
    try:
        run_scraping_job(db, job_id)
    except Exception as exc:
        logger.error("inline_scrape_failed", job_id=job_id, error=str(exc))
    finally:
        db.close()


def enqueue_task(task, *args) -> str:
    if broker_available():
        task.delay(*args)
        return "celery"
    threading.Thread(target=task, args=args, daemon=True).start()
    logger.warning("task_inline_no_redis", task=getattr(task, "name", str(task)))
    return "inline"


def dispatch_scrape_job(db: Session, job: ScrapingJob) -> ScrapingJob:
    if broker_available():
        from workers.tasks.scraping import scrape_google_maps_job

        scrape_google_maps_job.delay(job.id)
        job.worker_status = "queued"
        job.error_message = ""
    else:
        job.worker_status = "running locally"
        job.error_message = ""
        threading.Thread(target=_run_job_inline, args=(job.id,), daemon=True).start()
        logger.warning("scrape_inline_no_redis", job_id=job.id)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def run_scraping_job(db: Session, job_id: str) -> None:
    job = db.get(ScrapingJob, job_id)
    if not job:
        logger.error("job_missing", job_id=job_id)
        return
    job.status = "running"
    job.started_at = job.started_at or datetime.now(UTC)
    job.worker_status = "running"
    db.commit()

    searches = (
        db.query(ScrapingSearch)
        .filter(ScrapingSearch.job_id == job_id)
        .order_by(ScrapingSearch.sort_order)
        .all()
    )
    user_settings = db.query(UserSettings).filter(UserSettings.user_id == job.user_id).first()
    headless = user_settings.headless_browser if user_settings else None
    apify_token = resolve_apify_token(db, job.user_id)
    use_apify = resolve_scraper_provider(db, job.user_id) == "apify"
    try:
        if use_apify and not apify_token:
            raise ApifyError(APIFY_TOKEN_MISSING)
        job.worker_status = "scraping via Apify" if use_apify else "scraping Google Maps"
        db.commit()
        for search in searches:
            if not _refresh_job(db, job) or job.status == "cancelled":
                return
            while job.status == "paused":
                job.worker_status = "paused"
                db.commit()
                import time

                time.sleep(2)
                if not _refresh_job(db, job) or job.status == "cancelled":
                    return
            if not _refresh_job(db, job) or job.status == "cancelled":
                return
            if search.status == "completed":
                continue
            _run_search(db, job, search, use_apify=use_apify, apify_token=apify_token, headless=headless)
            db.refresh(search)
            if not _refresh_job(db, job) or job.status in {"cancelled", "blocked"}:
                return
        if not _refresh_job(db, job):
            return
        if job.status not in {"cancelled", "blocked", "failed"}:
            job.status = "completed"
            job.worker_status = "idle"
            job.finished_at = datetime.now(UTC)
        db.commit()
        from app.services.email_recheck import start_recheck

        start_recheck(job.id, discard_missing=True)  # anything still waiting for its email search
    except Exception as exc:
        logger.error("job_failed", job_id=job_id, error=str(exc))
        if not _refresh_job(db, job):
            return
        job.status = "failed"
        job.error_message = str(exc)[:240] if isinstance(exc, ApifyError) else short_browser_error(exc)
        job.worker_status = "failed"
        job.finished_at = datetime.now(UTC)
        db.commit()


def _run_search(
    db: Session,
    job: ScrapingJob,
    search: ScrapingSearch,
    use_apify: bool = False,
    apify_token: str = "",
    headless: bool | None = None,
) -> None:
    search.status = "running"
    job.current_search_id = search.id
    job.current_query = search.query
    job.worker_status = "scraping"
    db.commit()
    pending_website_ids: list[str] = []

    def control() -> str:
        if not _refresh_job(db, job):
            return "cancelled"
        return job.status

    def progress(payload: dict) -> None:
        search.current_page = payload.get("page", search.current_page)
        job.current_query = payload.get("query", job.current_query)
        job.worker_status = payload.get("status", job.worker_status)
        db.commit()

    def save(business: ScrapedBusiness) -> None:
        if not _refresh_job(db, job) or job.status == "cancelled":
            raise ScrapingCancelledError("Scraping cancelled")
        search.businesses_found += 1
        job.businesses_found += 1
        email = str((business.extra or {}).get("email") or "").strip()
        has_email = bool(email) and "@" in email
        if not has_email and not (business.website or "").strip():
            # No email and no website to find one on: not a lead for email outreach, so it isn't saved
            from sqlalchemy.orm.attributes import flag_modified

            extra = dict(job.extra or {})
            extra["discarded_no_contact"] = int(extra.get("discarded_no_contact") or 0) + 1
            job.extra = extra
            flag_modified(job, "extra")
            db.commit()
            return
        existing = find_duplicate_lead(
            db,
            job.user_id,
            google_place_id=business.google_place_id,
            website_url=business.website,
            phone=business.phone,
            business_name=business.business_name,
            address=business.address,
        )
        if existing:
            search.duplicate_leads += 1
            job.duplicate_leads += 1
            db.commit()
            return
        lead = persist_lead(
            db,
            job.user_id,
            {
                "business_name": business.business_name,
                "category": business.category or search.category,
                "country": search.country,
                "city": search.city,
                "address": business.address,
                "primary_phone": business.phone,
                "website_url": business.website,
                "google_maps_url": business.google_maps_url,
                "google_place_id": business.google_place_id,
                "rating": business.rating,
                "review_count": business.review_count,
                "source_search": search.query,
                "primary_email": email if has_email else "",
                "contact_name": (business.extra or {}).get("contact_name") or "",
                "extra": business.extra,
                # No email yet: held back (not listed) while its website gets one more thorough email search
                # (email_recheck, after the search); discarded if none is found.
                **({} if has_email else {"status": "no_email"}),
            },
            source="google_maps",
            scraping_job_id=job.id,
            allow_without_email=True,
        )
        if not lead:
            return
        if has_email:
            search.new_leads += 1
            job.new_leads += 1
        else:
            from sqlalchemy.orm.attributes import flag_modified

            extra = dict(job.extra or {})
            extra["skipped_no_email"] = int(extra.get("skipped_no_email") or 0) + 1
            job.extra = extra
            flag_modified(job, "extra")
        db.commit()
        if has_email and lead.website_url and job.outreach_status == "running":
            # The scraper already found the email and checked the site: start this lead's audit -> PDF -> email now,
            # while scraping goes on (the file's daily limit and the shared Claude capacity still apply).
            from app.services.auto_audits import request_poll

            request_poll()
        # The website analysis (platform, score, more emails) still runs after the search, alongside the audits.
        # Leads without an email go through the email search first (it adds them here when it finds one).
        if lead.website_url and has_email:
            pending_website_ids.append(lead.id)

    try:
        if use_apify:
            scrape_google_maps_apify(
                country=search.country,
                city=search.city,
                category=search.category,
                max_results=job.max_results_per_search,
                save=save,
                progress=progress,
                control=control,
                token=apify_token,
            )
        else:
            scrape_google_maps(
                country=search.country,
                city=search.city,
                category=search.category,
                max_results=job.max_results_per_search,
                save=save,
                progress=progress,
                control=control,
                headless=headless,
            )
        search.status = "completed"
        db.commit()
    except ScrapingBlockedError as exc:
        search.status = "blocked"
        search.error_message = str(exc)
        job.status = "blocked"
        job.error_message = str(exc)
        job.worker_status = "blocked"
        job.errors_count += 1
        job.finished_at = datetime.now(UTC)
        db.commit()
        logger.error("scrape_blocked", job_id=job.id, search_id=search.id)
    except ScrapingCancelledError:
        search.status = "cancelled"
        db.commit()
    except Exception as exc:
        search.status = "failed"
        message = str(exc)[:240] if isinstance(exc, ApifyError) else short_browser_error(exc)
        search.error_message = message
        job.errors_count += 1
        job.error_message = message
        db.commit()
        logger.error("search_failed", job_id=job.id, search_id=search.id, error=str(exc))
    from app.services.lead_pipeline import enqueue_lead_website_processing

    enqueue_lead_website_processing(pending_website_ids)
    # One more, thorough email search on the websites of this search's leads without one; the rest are discarded
    from app.services.email_recheck import start_recheck

    start_recheck(job.id, discard_missing=True)
