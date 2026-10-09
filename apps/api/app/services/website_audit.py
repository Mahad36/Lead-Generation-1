"""Website audit workflow.

fetch website + customer data -> measure performance -> Claude audit -> PDF -> Claude email -> send with PDF.
Each step updates `WebsiteAudit.status`/`step` so the UI can show progress, and any failure is
stored on the audit (never raised out of the background task).
"""

from __future__ import annotations

import shutil
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup
from email_validator import EmailNotValidError, validate_email
from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_config import get_logger
from app.models import EmailAccount, EmailMessage, Lead, UserService, UserSettings, WebsiteAudit
from app.services.ai import (
    AICancelledError,
    AINotConfiguredError,
    AIProviderError,
    cancel_check,
    get_ai_provider_for_user,
)
from app.services.analysis import analyze_website_signals
from app.services.audit import write_audit
from app.services.audit_ai import generate_audit_email, generate_audit_report
from app.services.audit_pdf import build_audit_pdf, pdf_filename
from app.services.crawler import crawl_website
from app.services.gmail import EmailAttachment, GmailSendError, daily_limit_reached, send_gmail_message
from app.services.integrations import resolve_ai_config
from app.services.performance import PerformanceBlockedError, PerformanceError, measure_performance
from app.services.portfolio import for_prompt as portfolio_for_prompt
from app.services.portfolio import keep_known
from app.services.screenshots import ScreenshotError, capture_screenshots
from app.services.platform_detection import detect_platform
from app.services.suppression import is_suppressed
from app.utils.normalize import normalize_email, normalize_url

logger = get_logger("website_audit")

# Ordered workflow steps; the UI renders these as a progress list.
STEPS = ["fetching", "measuring", "capturing", "analyzing", "generating_pdf", "drafting_email"]
STEP_LABELS = {
    "queued": "Waiting for a worker",
    "fetching": "Fetching website and customer data",
    "measuring": "Measuring speed and page weight",
    "capturing": "Taking screenshots (desktop and phone)",
    "analyzing": "Claude is analysing the website",
    "generating_pdf": "Building the PDF audit",
    "drafting_email": "Claude is writing the email",
    "ready": "Ready to review and send",
    "sending": "Sending email with PDF",
    "sent": "Email sent",
    "send_failed": "Sending failed",
    "failed": "Audit failed",
    "cancelled": "Stopped by you",
}
ACTIVE_STATUSES = {"queued", *STEPS, "sending"}
WAITING_FOR_LIMIT = "Waiting: daily send limit reached (sends automatically when allowed)"
SENDABLE_STATUSES = {"ready", "send_failed", "sent"}
STALE_AFTER = timedelta(minutes=20)


class AuditSendError(Exception):
    pass


class AuditStopped(Exception):
    """The user clicked "Stop audit"."""


# Audits the user asked to stop (checked between steps and while Claude is working).
_stop_requests: set[str] = set()


def _stop_requested(db: Session, audit_id: str) -> bool:
    if audit_id in _stop_requests:
        return True
    # Another process (e.g. a Celery worker) may hold the request: the database is the source of truth.
    return db.query(WebsiteAudit.status).filter(WebsiteAudit.id == audit_id).scalar() == "cancelled"


def cancel_audit(db: Session, user_id: str, audit_id: str, meta: dict | None = None) -> WebsiteAudit:
    """Stop a running audit: nothing more is done and nothing is sent. A new audit can be started any time."""
    audit = get_audit(db, user_id, audit_id)
    if audit.status not in ACTIVE_STATUSES or audit.status == "sending":
        raise HTTPException(status_code=400, detail="This audit is not running.")
    audit.failed_step = audit.status
    audit.status, audit.step, audit.error_message = "cancelled", STEP_LABELS["cancelled"], ""
    audit.auto_send = False
    _stop_requests.add(audit.id)
    write_audit(db, user_id=user_id, action="website_audit.cancelled", entity_type="website_audit",
                entity_id=audit.id, details={"lead_id": audit.lead_id, "step": audit.failed_step}, **(meta or {}))
    db.commit()
    db.refresh(audit)
    logger.info("audit_cancelled", audit_id=audit.id, step=audit.failed_step)
    _sync_campaign(db, audit)
    return audit


# ---------------------------------------------------------------- helpers


def get_audit(db: Session, user_id: str, audit_id: str) -> WebsiteAudit:
    audit = db.query(WebsiteAudit).filter(WebsiteAudit.id == audit_id, WebsiteAudit.user_id == user_id).first()
    if not audit:
        raise HTTPException(status_code=404, detail="Audit not found")
    return audit


def list_audits(
    db: Session, user_id: str, lead_id: str | None = None, limit: int = 50, q: str | None = None
) -> list[WebsiteAudit]:
    query = db.query(WebsiteAudit).filter(WebsiteAudit.user_id == user_id)
    if lead_id:
        query = query.filter(WebsiteAudit.lead_id == lead_id)
    if q and q.strip():
        # Website, customer email or business name, anywhere in the text, any case
        like = f"%{q.strip()}%"
        query = query.outerjoin(Lead, Lead.id == WebsiteAudit.lead_id).filter(
            or_(
                WebsiteAudit.website_url.ilike(like),
                WebsiteAudit.recipient_email.ilike(like),
                Lead.business_name.ilike(like),
                Lead.primary_email.ilike(like),
            )
        )
    return query.order_by(WebsiteAudit.created_at.desc()).limit(limit).all()


def _valid_email(value: str) -> str:
    email = normalize_email(value)
    if not email:
        return ""
    try:
        return validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid email address: {email}") from exc


DEFAULT_FREE_OFFERS = [
    "A free redesign of the first screen of your homepage, as a Figma mockup",
    "A free landing page design for your main service",
    "A free 10-minute screen-recorded walkthrough of the fixes on your own site",
]


def free_offers(settings: UserSettings | None) -> list[str]:
    """The user's free offers (Settings, one per line), or sensible defaults."""
    lines = [line.strip(" -•\t") for line in ((settings.free_offers if settings else "") or "").splitlines()]
    return [line for line in lines if line] or DEFAULT_FREE_OFFERS


def _sender_info(db: Session, user_id: str) -> dict:
    settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    services = [s.name for s in db.query(UserService).filter(UserService.user_id == user_id).all()]
    return {
        "sender_name": settings.sender_name if settings else "",
        "company_name": settings.company_name if settings else "",
        "company_website": settings.company_website if settings else "",
        "company_description": settings.company_description if settings else "",
        "contact_email": settings.contact_email if settings else "",
        "services": services,
        "free_offers": free_offers(settings),
    }


def _lead_info(lead: Lead) -> dict:
    return {
        "business_name": lead.business_name,
        "contact_name": lead.contact_name,
        "category": lead.category,
        "city": lead.city,
        "country": lead.country,
        "website_url": lead.website_url,
        "rating": lead.rating,
        "review_count": lead.review_count,
    }


def _expire_stale(db: Session, audit: WebsiteAudit) -> bool:
    """Mark an audit failed if its worker died mid-run."""
    if audit.status not in ACTIVE_STATUSES:
        return False
    updated = audit.updated_at
    if updated and updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    if updated and datetime.now(UTC) - updated > STALE_AFTER:
        audit.failed_step = audit.status
        audit.status = "failed"
        audit.step = STEP_LABELS["failed"]
        audit.error_message = "The audit stopped responding (worker restarted or timed out). Run it again."
        db.commit()
        return True
    return False


def _tracking(audit: WebsiteAudit) -> dict:
    from sqlalchemy.orm import object_session

    from app.services.tracking import tracking_summary

    db = object_session(audit)
    message = db.get(EmailMessage, audit.email_message_id) if db and audit.email_message_id else None
    return tracking_summary(message)


def serialize_audit(audit: WebsiteAudit, *, full: bool = True) -> dict:
    data = {
        "id": audit.id,
        "lead_id": audit.lead_id,
        "status": audit.status,
        "step": audit.step or STEP_LABELS.get(audit.status, audit.status),
        "failed_step": audit.failed_step,
        "error_message": audit.error_message,
        "website_url": audit.website_url,
        "recipient_email": audit.recipient_email,
        "auto_send": audit.auto_send,
        "trigger": audit.trigger,
        "overall_score": audit.overall_score,
        "headline": (audit.report or {}).get("headline", ""),
        "has_pdf": bool(audit.pdf_size),
        "has_report": bool(audit.report),
        "pdf_filename": audit.pdf_filename,
        "pdf_size": audit.pdf_size,
        "ai_provider": audit.ai_provider,
        "ai_model": audit.ai_model,
        "created_at": audit.created_at,
        "updated_at": audit.updated_at,
        "generated_at": audit.generated_at,
        "sent_at": audit.sent_at,
        **_tracking(audit),
    }
    if full:
        perf = audit.performance or {}
        data.update(
            {
                "report": audit.report or {},
                "resources": {
                    key: (perf.get("resources") or {}).get(key)
                    for key in ("total_requests", "total_bytes", "by_type", "third_party_requests")
                },
                "probe": perf.get("probe") or {},
                "email_subject": audit.email_subject,
                "email_html": audit.email_html,
                "email_account_id": audit.email_account_id,
            }
        )
    return data


# ---------------------------------------------------------------- start


def start_audit(
    db: Session,
    user_id: str,
    lead_id: str,
    *,
    recipient_email: str | None = None,
    auto_send: bool = False,
    email_account_id: str | None = None,
    website_url: str | None = None,
    trigger: str = "manual",
    meta: dict | None = None,
) -> WebsiteAudit:
    from app.services.campaigns import resolve_email_account
    from app.services.leads import get_lead

    lead = get_lead(db, user_id, lead_id)
    # An explicitly entered URL wins over the one stored on the lead.
    target_url = normalize_url((website_url or lead.website_url or "").strip())
    if not target_url:
        raise HTTPException(status_code=400, detail="This lead has no website URL. Add one before running an audit.")

    running = (
        db.query(WebsiteAudit)
        .filter(WebsiteAudit.lead_id == lead.id, WebsiteAudit.status.in_(ACTIVE_STATUSES))
        .order_by(WebsiteAudit.created_at.desc())
        .first()
    )
    if running and not _expire_stale(db, running):
        raise HTTPException(status_code=409, detail="An audit for this lead is already running.")

    recipient = _valid_email(recipient_email or lead.primary_email)
    if auto_send:
        if not recipient:
            raise HTTPException(status_code=400, detail="Add the customer's email address to send automatically.")
        email_account_id = resolve_email_account(db, user_id, email_account_id).id

    audit = WebsiteAudit(
        user_id=user_id,
        lead_id=lead.id,
        website_url=target_url,
        trigger=trigger,
        recipient_email=recipient,
        auto_send=auto_send,
        email_account_id=email_account_id,
        status="queued",
        step=STEP_LABELS["queued"],
    )
    db.add(audit)
    db.flush()
    write_audit(
        db,
        user_id=user_id,
        action="website_audit.started",
        entity_type="website_audit",
        entity_id=audit.id,
        details={"lead_id": lead.id, "auto_send": auto_send, "trigger": trigger},
        **(meta or {}),
    )
    db.commit()
    db.refresh(audit)

    from app.services.scraping_jobs import enqueue_task
    from workers.tasks.audits import run_website_audit

    mode = enqueue_task(run_website_audit, audit.id)
    logger.info("audit_queued", audit_id=audit.id, lead_id=lead.id, mode=mode)
    return audit


# ---------------------------------------------------------------- run


def _set_step(db: Session, audit: WebsiteAudit, status: str) -> None:
    if _stop_requested(db, audit.id):
        raise AuditStopped()
    audit.status = status
    audit.step = STEP_LABELS[status]
    db.commit()
    logger.info("audit_step", audit_id=audit.id, step=status)


def _collect_site_data(lead: Lead, url: str) -> dict:
    crawl = crawl_website(url, max_pages=3, timeout=15)
    homepage = crawl.homepage
    html = homepage.html if homepage else ""
    detection = detect_platform(html=html, headers=crawl.headers, url=url)
    text = " ".join(page.text for page in crawl.pages)[:20000]
    analysis = analyze_website_signals(
        text=text,
        title=homepage.title if homepage else "",
        meta_description=homepage.meta_description if homepage else "",
        signals=crawl.signals,
        pages=[{"url": p.url, "status_code": p.status_code} for p in crawl.pages],
        platform=detection.platform_name,
        city=lead.city,
        category=lead.category,
        business_name=lead.business_name,
    )
    return {
        "url": url,
        "reachable": bool(homepage and homepage.status_code and homepage.status_code < 400),
        "crawl_error": crawl.error,
        "pages_crawled": [{"url": p.url, "status_code": p.status_code, "title": p.title} for p in crawl.pages],
        "title": homepage.title if homepage else "",
        "meta_description": homepage.meta_description if homepage else "",
        "platform": detection.platform_name,
        "platform_confidence": detection.confidence,
        "technologies": [t["name"] for t in detection.technologies][:15],
        "social_links": crawl.social,
        "emails_found": [e["email"] for e in crawl.emails][:5],
        "business_summary": analysis.get("business_summary", ""),
        "services": analysis.get("services", [])[:10],
        "rule_based_issues": analysis.get("issues", []),
        "rule_based_opportunities": analysis.get("opportunities", []),
        "rule_based_scores": analysis.get("category_scores", {}),
        "rule_based_website_score": analysis.get("website_score"),
        "text_excerpt": text[:3000],
    }


AUDIT_FILES_KEEP_DAYS = 30


def audit_files_dir(audit_id: str) -> Path:
    """Screenshots of one audit, kept so the PDF can be rebuilt without re-visiting the site."""
    base = get_settings().audit_files_dir.strip()
    root = Path(base) if base else Path(tempfile.gettempdir()) / "leadgen-audits"
    folder = root / audit_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _prune_audit_files() -> None:
    base = get_settings().audit_files_dir.strip()
    root = Path(base) if base else Path(tempfile.gettempdir()) / "leadgen-audits"
    cutoff = time.time() - AUDIT_FILES_KEEP_DAYS * 86400
    try:
        for folder in root.iterdir():
            if folder.is_dir() and folder.stat().st_mtime < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
    except OSError:
        pass


def _saved_screenshots(audit_id: str) -> dict:
    folder = audit_files_dir(audit_id)
    return {
        device: sorted(str(p) for p in folder.glob(f"{device}-*.png"))
        for device in ("desktop", "mobile")
        if any(folder.glob(f"{device}-*.png"))
    }


def _build_pdf(audit: WebsiteAudit, lead: Lead, sender: dict, screenshots: dict) -> None:
    generated_at = datetime.now(UTC)
    pdf = build_audit_pdf(
        report=audit.report or {},
        performance=audit.performance or {},
        lead=_lead_info(lead) | {"website_url": audit.website_url},
        sender=sender,
        generated_at=generated_at,
        ai_model=audit.ai_model if audit.ai_provider in {"anthropic", "claude_code"} else "",
        screenshots=screenshots,
    )
    audit.pdf_data = pdf
    audit.pdf_size = len(pdf)
    audit.pdf_filename = pdf_filename(lead.business_name, generated_at)
    audit.generated_at = generated_at


def run_audit(db: Session, audit_id: str) -> str:
    audit = db.get(WebsiteAudit, audit_id)
    if not audit:
        logger.warning("audit_missing", audit_id=audit_id)
        return "missing"
    if audit.status != "queued":
        logger.info("audit_skip_not_queued", audit_id=audit_id, status=audit.status)
        return audit.status
    lead = db.get(Lead, audit.lead_id)
    log = logger.bind(audit_id=audit.id, lead_id=audit.lead_id)
    if not lead:
        audit.status, audit.error_message = "failed", "The lead was deleted."
        db.commit()
        return "failed"

    audit.started_at = datetime.now(UTC)
    cancel_check.set(lambda: audit_id in _stop_requests)  # ends a running Claude review on "Stop audit"
    _prune_audit_files()
    shots_dir = str(audit_files_dir(audit.id))
    lead_info = _lead_info(lead) | {"website_url": audit.website_url}
    settings = get_settings()
    current = "fetching"
    try:
        _set_step(db, audit, "fetching")
        site = _collect_site_data(lead, audit.website_url)
        audit.site_data = site
        db.commit()

        current = "measuring"
        _set_step(db, audit, "measuring")
        try:
            performance = measure_performance(audit.website_url)
        except PerformanceBlockedError as exc:
            # The site is up but its firewall refuses automated speed tests; carry on with the screenshots review.
            log.warning("audit_measure_blocked", error=str(exc))
            performance = {"url": audit.website_url, "measured_at": datetime.now(UTC).isoformat(), "blocked": str(exc)}
        audit.performance = performance
        db.commit()

        current = "capturing"
        _set_step(db, audit, "capturing")
        screenshots: dict = {}
        try:
            screenshots = capture_screenshots(audit.website_url, shots_dir)
        except ScreenshotError as exc:
            # The audit still runs on the measured data; the report says the visual review was skipped.
            log.warning("audit_screenshots_skipped", error=str(exc))
            site["screenshot_error"] = str(exc)
        audit.site_data = site | {"screenshots": {k: len(v) for k, v in screenshots.items()}}
        db.commit()
        images = screenshots.get("desktop", []) + screenshots.get("mobile", [])

        current = "analyzing"
        _set_step(db, audit, "analyzing")
        config = resolve_ai_config(db, audit.user_id)
        provider = get_ai_provider_for_user(db, audit.user_id)
        audit.ai_provider, audit.ai_model = config.provider, config.model
        portfolio_sites = portfolio_for_prompt(db, audit.user_id)
        report = generate_audit_report(
            provider,
            lead=lead_info,
            site={k: v for k, v in site.items() if k != "text_excerpt"} | {"text_excerpt": site["text_excerpt"][:1500]},
            performance=performance,
            timeout=settings.audit_ai_timeout_seconds,
            images=images,
            free_offers=_sender_info(db, audit.user_id)["free_offers"],
            portfolio=portfolio_sites,
        )
        report["reference_sites"] = keep_known(report.get("reference_sites") or [], portfolio_sites)
        audit.report = report
        audit.overall_score = report.get("overall_score")
        db.commit()

        current = "generating_pdf"
        _set_step(db, audit, "generating_pdf")
        sender = _sender_info(db, audit.user_id)
        _build_pdf(audit, lead, sender, screenshots)
        db.commit()

        current = "drafting_email"
        _set_step(db, audit, "drafting_email")
        email = generate_audit_email(
            provider,
            lead=lead_info,
            report=report,
            sender=sender,
            timeout=settings.audit_ai_timeout_seconds,
        )
        audit.email_subject = email["subject"]
        audit.email_html = email["html"]
        _set_step(db, audit, "ready")
        log.info("audit_ready", score=audit.overall_score, pdf_bytes=audit.pdf_size)
    except Exception as exc:  # every failure is recorded on the audit
        db.rollback()
        audit = db.get(WebsiteAudit, audit_id)
        if isinstance(exc, (AuditStopped, AICancelledError)) or _stop_requested(db, audit_id):
            # Stopped by the user: cancel_audit already recorded it; just clean up.
            if audit.status != "cancelled":
                audit.failed_step = current
                audit.status, audit.step, audit.auto_send = "cancelled", STEP_LABELS["cancelled"], False
                db.commit()
            _stop_requests.discard(audit_id)
            shutil.rmtree(shots_dir, ignore_errors=True)
            log.info("audit_stopped", step=current)
            return "cancelled"
        audit.status = "failed"
        audit.failed_step = current
        audit.step = STEP_LABELS["failed"]
        audit.error_message = _friendly_error(exc)
        db.commit()
        log.error("audit_failed", step=current, error=str(exc), error_type=exc.__class__.__name__)
        shutil.rmtree(shots_dir, ignore_errors=True)
        _sync_campaign(db, audit)
        return "failed"

    hold = _campaign_hold(db, audit) if audit.auto_send else ""
    if hold:
        from app.services.campaigns import CAMPAIGN_PAUSED_STEP, CAMPAIGN_STOPPED_STEP

        audit.step = CAMPAIGN_PAUSED_STEP if hold == "paused" else CAMPAIGN_STOPPED_STEP
        if hold == "stopped":
            audit.auto_send = False
        db.commit()
        log.info("audit_send_held", reason=f"outreach {hold}")
    elif audit.auto_send:
        try:
            send_audit(db, audit.user_id, audit.id)
        except (AuditSendError, HTTPException) as exc:
            db.rollback()
            log.warning("audit_auto_send_failed", error=str(getattr(exc, "detail", exc)))
            audit = db.get(WebsiteAudit, audit_id)
            if isinstance(exc, HTTPException) and exc.status_code == 429:
                # Sent automatically by the poller once the daily limit allows (see auto_audits.send_waiting).
                audit.step = WAITING_FOR_LIMIT
                db.commit()
    _sync_campaign(db, audit)
    return audit.status


def _portfolio_links(audit: WebsiteAudit) -> list[str]:
    """Reference-site links stay real, visible URLs (never routed through click tracking)."""
    links = []
    for item in (audit.report or {}).get("reference_sites") or []:
        url = str(item.get("url") or "").rstrip("/")
        if url:
            links += [url, url.replace("https://", "http://", 1), url.replace("://", "://www.", 1)]
    return links


def _campaign_hold(db: Session, audit: WebsiteAudit) -> str:
    try:
        from app.services.auto_audits import outreach_hold  # covers campaigns and per-file outreach

        return outreach_hold(db, audit)
    except Exception as exc:  # bookkeeping must never block an audit
        logger.warning("campaign_hold_check_failed", audit_id=audit.id, error=str(exc))
        return ""


def _sync_campaign(db: Session, audit: WebsiteAudit) -> None:
    """Let an audit campaign know this lead's audit finished / was sent."""
    try:
        from app.services.campaigns import sync_campaign_lead_for_audit

        sync_campaign_lead_for_audit(db, audit)
    except Exception as exc:  # never fail the audit because of campaign bookkeeping
        db.rollback()
        logger.warning("campaign_sync_failed", audit_id=audit.id, error=str(exc))


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, AINotConfiguredError):
        if "Claude Code" in str(exc):
            return str(exc)
        return (
            "AI is not configured. In AI & settings choose Claude Code (your subscription) or add an API key, "
            "then run the audit again."
        )
    if isinstance(exc, (AIProviderError, PerformanceError)):
        return str(exc)
    return f"Unexpected error ({exc.__class__.__name__}). Check the API/worker logs for details."


# ---------------------------------------------------------------- regenerate single steps


def _require_report(db: Session, audit: WebsiteAudit) -> None:
    if audit.status in ACTIVE_STATUSES and not _expire_stale(db, audit):
        raise HTTPException(status_code=409, detail="This audit is still running. Wait for it to finish.")
    if not audit.report:
        raise HTTPException(status_code=400, detail="There is no audit report yet. Re-run the website audit first.")


def regenerate_pdf(db: Session, user_id: str, audit_id: str, meta: dict | None = None) -> WebsiteAudit:
    """Rebuild the PDF from the stored report, measurements and screenshots (e.g. after changing sender details)."""
    audit = get_audit(db, user_id, audit_id)
    _require_report(db, audit)
    lead = db.get(Lead, audit.lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="The lead was deleted.")
    _build_pdf(audit, lead, _sender_info(db, user_id), _saved_screenshots(audit.id))
    if audit.status == "failed" and audit.email_html:
        audit.status, audit.step, audit.failed_step, audit.error_message = "ready", STEP_LABELS["ready"], "", ""
    write_audit(db, user_id=user_id, action="website_audit.pdf_regenerated", entity_type="website_audit",
                entity_id=audit.id, details={"lead_id": audit.lead_id}, **(meta or {}))
    db.commit()
    db.refresh(audit)
    logger.info("audit_pdf_regenerated", audit_id=audit.id, pdf_bytes=audit.pdf_size)
    return audit


def start_email_redraft(db: Session, user_id: str, audit_id: str, meta: dict | None = None) -> WebsiteAudit:
    """Ask Claude for a new personalised email (background); the PDF is rebuilt first if it is missing."""
    audit = get_audit(db, user_id, audit_id)
    _require_report(db, audit)
    audit.status, audit.step = "drafting_email", STEP_LABELS["drafting_email"]
    audit.failed_step, audit.error_message = "", ""
    write_audit(db, user_id=user_id, action="website_audit.email_regenerated", entity_type="website_audit",
                entity_id=audit.id, details={"lead_id": audit.lead_id}, **(meta or {}))
    db.commit()
    db.refresh(audit)

    from app.services.scraping_jobs import enqueue_task
    from workers.tasks.audits import redraft_audit_email

    enqueue_task(redraft_audit_email, audit.id)
    return audit


def redraft_email(db: Session, audit_id: str) -> str:
    audit = db.get(WebsiteAudit, audit_id)
    if not audit or audit.status != "drafting_email":
        return audit.status if audit else "missing"
    lead = db.get(Lead, audit.lead_id)
    log = logger.bind(audit_id=audit.id, lead_id=audit.lead_id)
    try:
        if not lead:
            raise HTTPException(status_code=404, detail="The lead was deleted.")
        sender = _sender_info(db, audit.user_id)
        if not audit.pdf_data:
            _build_pdf(audit, lead, sender, _saved_screenshots(audit.id))
        email = generate_audit_email(
            get_ai_provider_for_user(db, audit.user_id),
            lead=_lead_info(lead) | {"website_url": audit.website_url},
            report=audit.report or {},
            sender=sender,
            timeout=get_settings().audit_ai_timeout_seconds,
        )
        audit.email_subject, audit.email_html = email["subject"], email["html"]
        _set_step(db, audit, "ready")
        log.info("audit_email_redrafted")
        return "ready"
    except Exception as exc:
        db.rollback()
        audit = db.get(WebsiteAudit, audit_id)
        audit.status, audit.failed_step, audit.step = "failed", "drafting_email", STEP_LABELS["failed"]
        audit.error_message = str(exc.detail) if isinstance(exc, HTTPException) else _friendly_error(exc)
        db.commit()
        log.error("audit_email_redraft_failed", error=str(exc))
        return "failed"


# ---------------------------------------------------------------- edit + send


def update_draft(
    db: Session,
    user_id: str,
    audit_id: str,
    *,
    email_subject: str | None = None,
    email_html: str | None = None,
    recipient_email: str | None = None,
) -> WebsiteAudit:
    audit = get_audit(db, user_id, audit_id)
    if audit.status not in SENDABLE_STATUSES:
        raise HTTPException(status_code=400, detail="The audit is not ready yet.")
    if email_subject is not None:
        audit.email_subject = email_subject.strip()[:500]
    if email_html is not None:
        audit.email_html = email_html
    if recipient_email is not None:
        audit.recipient_email = _valid_email(recipient_email)
    db.commit()
    db.refresh(audit)
    return audit


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    blocks = [p.get_text(" ", strip=True) for p in soup.find_all(["p", "li"])]
    text = "\n\n".join(b for b in blocks if b) or soup.get_text("\n", strip=True)
    return text


def send_audit(
    db: Session,
    user_id: str,
    audit_id: str,
    *,
    recipient_email: str | None = None,
    email_subject: str | None = None,
    email_html: str | None = None,
    email_account_id: str | None = None,
    meta: dict | None = None,
) -> WebsiteAudit:
    from app.services.campaigns import resolve_email_account
    from app.services.tracking import (
        new_rfc_message_id,
        open_pixel_html,
        track_links,
        tracks_opens,
        unsubscribe_url,
    )

    audit = get_audit(db, user_id, audit_id)
    if audit.status not in SENDABLE_STATUSES:
        raise HTTPException(status_code=400, detail="The audit is not ready to send yet.")
    lead = db.get(Lead, audit.lead_id)
    # Validate the effective values first; nothing is saved unless every check passes.
    subject = (email_subject if email_subject is not None else audit.email_subject).strip()[:500]
    body_html = email_html if email_html is not None else audit.email_html
    recipient = _valid_email(
        recipient_email if recipient_email is not None else (audit.recipient_email or (lead.primary_email if lead else ""))
    )
    if not recipient:
        raise HTTPException(status_code=400, detail="Add the customer's email address first.")
    if not subject or not (body_html or "").strip():
        raise HTTPException(status_code=400, detail="The email subject and body are required.")
    pdf = audit.pdf_data
    if not pdf:
        raise HTTPException(status_code=400, detail="The PDF audit is missing. Run the audit again.")
    if lead and lead.do_not_contact:
        raise HTTPException(status_code=400, detail="This lead is marked do not contact.")
    settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    if (not settings or settings.respect_suppression_list) and is_suppressed(db, user_id, recipient):
        raise HTTPException(status_code=400, detail="This address is on your suppression list (unsubscribed).")

    account: EmailAccount = resolve_email_account(db, user_id, email_account_id or audit.email_account_id)
    if daily_limit_reached(db, account):
        raise HTTPException(
            status_code=429,
            detail=(
                f"Daily sending limit reached for {account.email_address} ({account.daily_limit} per day). "
                "It resets tomorrow; change it in AI & settings → Sending."
            ),
        )

    unsubscribe = unsubscribe_url(user_id, recipient)
    rfc_message_id = new_rfc_message_id(account.email_address)
    html_body = (
        f"{body_html}"
        f'<p style="font-size:12px;color:#666">Not interested? <a href="{unsubscribe}">Unsubscribe</a></p>'
    )
    text_body = f"{_html_to_text(body_html)}\n\nUnsubscribe: {unsubscribe}"
    tracked = tracks_opens(db, user_id)  # adds the open-tracking image; the visible email is the same

    message = EmailMessage(
        user_id=user_id,
        lead_id=audit.lead_id,
        email_account_id=account.id,
        recipient_email=recipient,
        recipient_name=lead.business_name if lead else "",
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        status="sending",
        rfc_message_id=rfc_message_id,
    )
    db.add(message)
    audit.email_subject, audit.email_html, audit.recipient_email = subject, body_html, recipient
    audit.status, audit.step, audit.error_message = "sending", STEP_LABELS["sending"], ""
    audit.email_account_id = account.id
    audit.send_attempts = (audit.send_attempts or 0) + 1
    db.commit()

    log = logger.bind(audit_id=audit.id, lead_id=audit.lead_id, account_id=account.id)
    try:
        provider_id = send_gmail_message(
            db,
            account,
            to=recipient,
            subject=subject,
            html_body=(
                track_links(html_body, message.id, skip=(unsubscribe, *_portfolio_links(audit)))
                + open_pixel_html(message.id)
                if tracked
                else html_body
            ),
            text_body=text_body,
            attachments=[EmailAttachment(filename=audit.pdf_filename or "website-audit.pdf", content=pdf)],
            headers={"Message-ID": rfc_message_id},  # no bulk-mail headers: 1-to-1 email, not a list
        )
    except GmailSendError as exc:
        message.status, message.error_message = "failed", str(exc)
        audit.status, audit.step, audit.error_message = "send_failed", STEP_LABELS["send_failed"], str(exc)
        db.commit()
        log.error("audit_send_failed", error=str(exc), retryable=exc.retryable)
        _sync_campaign(db, audit)
        raise AuditSendError(str(exc)) from exc

    now = datetime.now(UTC)
    message.status, message.provider_message_id, message.sent_at = "sent", provider_id, now
    audit.status, audit.step, audit.sent_at = "sent", STEP_LABELS["sent"], now
    audit.email_message_id = message.id
    if lead:
        lead.last_contacted_at = now
        lead.email_status = "contacted"
    write_audit(
        db,
        user_id=user_id,
        action="website_audit.sent",
        entity_type="website_audit",
        entity_id=audit.id,
        details={"recipient": recipient, "message_id": message.id},
        **(meta or {}),
    )
    db.commit()
    db.refresh(audit)
    log.info("audit_sent", recipient=recipient, provider_message_id=provider_id)
    _sync_campaign(db, audit)
    return audit


def get_pdf(db: Session, user_id: str, audit_id: str) -> tuple[bytes, str]:
    audit = get_audit(db, user_id, audit_id)
    if not audit.pdf_data:
        raise HTTPException(status_code=404, detail="No PDF for this audit yet.")
    return audit.pdf_data, audit.pdf_filename or "website-audit.pdf"


def lead_for_quick_audit(db: Session, user_id: str, data, meta: dict | None = None) -> Lead:
    """Reuse the user's existing lead for this website, or create a manual one."""
    from app.schemas.leads import LeadCreate
    from app.services.duplicates import find_duplicate_lead
    from app.services.leads import create_lead

    email = _valid_email(data.email)
    website = normalize_url(data.website_url)
    existing = find_duplicate_lead(db, user_id, website_url=website, email=email)
    if existing:
        if not existing.website_url:
            existing.website_url = website
        if not existing.primary_email:
            existing.primary_email = email
        db.commit()
        logger.info("quick_audit_reused_lead", lead_id=existing.id)
        return existing
    return create_lead(
        db,
        user_id,
        LeadCreate(
            business_name=data.business_name.strip(),
            website_url=website,
            primary_email=email,
            city=data.city,
            country=data.country,
            category=data.category,
        ),
        meta or {},
    )
