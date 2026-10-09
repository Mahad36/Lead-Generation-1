"""Find missing emails: re-check the websites of a scrape's "No email" leads.

Uses the scraper's website check (homepage + contact/about pages, Cloudflare-protected addresses, DNS fallback),
in parallel, then a real browser for sites whose firewall refused it. A lead whose email is found becomes ready
and the automatic outreach poller is woken.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.config import get_settings
from app.logging_config import get_logger
from app.models import Lead, LeadEmail, ScrapingJob

logger = get_logger("email_recheck")
_running: set[str] = set()
_lock = threading.Lock()


def leads_missing_email(db: Session, job: ScrapingJob) -> list[Lead]:
    return (
        db.query(Lead)
        .filter(Lead.scraping_job_id == job.id, Lead.primary_email == "", Lead.website_url != "")
        .all()
    )


def recheck_job(db: Session, job: ScrapingJob) -> dict:
    from app.services.auto_audits import request_poll
    from app.services.scraper import browser_check_sites, needs_browser, website_contacts

    leads = leads_missing_email(db, job)
    if not leads:
        return {"checked": 0, "found": 0}
    with ThreadPoolExecutor(max_workers=max(1, get_settings().scraper_site_workers)) as pool:
        results = list(pool.map(website_contacts, [lead.website_url for lead in leads]))
    # Sites whose firewall refused the quick check get one visit from a real browser.
    retry = [lead.website_url for lead, result in zip(leads, results) if needs_browser(result)]
    try:
        rendered = browser_check_sites(retry)
    except Exception as exc:
        logger.warning("browser_check_failed", error=str(exc)[:200])
        rendered = {}
    for lead, result in zip(leads, results):
        result.update(rendered.get(lead.website_url) or {})
        if not result.get("website_error"):
            result.pop("website_error", None)
    found = 0
    for lead, result in zip(leads, results):
        lead.extra = {**(lead.extra or {}), **{k: v for k, v in result.items() if k != "website_error"}}
        if result.get("website_error"):
            lead.extra["website_error"] = result["website_error"]
        else:
            lead.extra.pop("website_error", None)
        flag_modified(lead, "extra")
        email = (result.get("email") or "").strip().lower()
        if not email:
            continue
        found += 1
        lead.primary_email, lead.email_status, lead.status = email, "found", "ready"
        known = {e.email for e in lead.emails}
        for address in result.get("emails") or [email]:
            if address not in known:
                db.add(LeadEmail(lead_id=lead.id, email=address, is_primary=address == email, source_url=lead.website_url))
                known.add(address)
    if found:
        extra = dict(job.extra or {})
        extra["skipped_no_email"] = max(0, int(extra.get("skipped_no_email") or 0) - found)
        job.extra = extra
        flag_modified(job, "extra")
        job.new_leads += found
    db.commit()
    logger.info("missing_emails_rechecked", job_id=job.id, checked=len(leads), found=found)
    if found:
        request_poll()
    return {"checked": len(leads), "found": found}


def start_recheck(job_id: str) -> bool:
    """Run in the background; False if this job is already being re-checked."""
    with _lock:
        if job_id in _running:
            return False
        _running.add(job_id)

    def run() -> None:
        from app.database import get_session

        db = get_session()
        try:
            job = db.get(ScrapingJob, job_id)
            if job:
                recheck_job(db, job)
        except Exception as exc:
            logger.error("missing_emails_recheck_failed", job_id=job_id, error=str(exc))
        finally:
            db.close()
            with _lock:
                _running.discard(job_id)

    threading.Thread(target=run, name="email-recheck", daemon=True).start()
    return True


def is_running(job_id: str) -> bool:
    return job_id in _running
