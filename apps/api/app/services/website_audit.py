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
from sqlalchemy import or_, update
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
from app.services.deliverability import (
    WAITING_FOR_INBOX,
    WAITING_FOR_SCHEDULE,
    WAITING_FOR_SPACING,
    WAITING_FOR_WINDOW,
)
from app.services.gmail import EmailAttachment, GmailSendError, send_gmail_message
from app.services.integrations import resolve_ai_config
from app.services.performance import PerformanceBlockedError, PerformanceError, measure_performance
from app.services.portfolio import for_prompt as portfolio_for_prompt
from app.services.portfolio import keep_known
from app.services.screenshots import ScreenshotError, capture_screenshots
from app.services.automations import candidates as automation_candidates
from app.services.automations import merge_signals, parse_offers, website_signals
from app.services.platform_detection import detect_platform
from app.services.suppression import is_suppressed
from app.utils.normalize import normalize_email, normalize_url
from app.services.contacts import company_already_contacted, recipient_options, select_recipients
from app.services.gmail_signature import account_signature
from app.utils.greeting import apply_greeting, owner_first_name
from app.utils.signature import append_signature, drop_ai_sign_off, ensure_sign_off

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
# Every reason an automatic send waits; auto_audits.send_waiting retries them all
from app.services.send_protection import WAITING_FOR_HEALTH  # noqa: E402

WAITING_STEPS = (
    WAITING_FOR_LIMIT, WAITING_FOR_WINDOW, WAITING_FOR_INBOX, WAITING_FOR_SPACING, WAITING_FOR_SCHEDULE, WAITING_FOR_HEALTH,
)
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
    db: Session,
    user_id: str,
    lead_id: str | None = None,
    limit: int = 50,
    q: str | None = None,
    offset: int = 0,
    platform: str | None = None,
    finding_type: str | None = None,
) -> list[WebsiteAudit]:
    query = _audits_query(db, user_id, lead_id, q, platform, finding_type)
    return query.order_by(WebsiteAudit.created_at.desc()).offset(offset).limit(limit).all()


def count_audits(
    db: Session, user_id: str, lead_id: str | None = None, q: str | None = None, platform: str | None = None,
    finding_type: str | None = None,
) -> int:
    return _audits_query(db, user_id, lead_id, q, platform, finding_type).count()


def audit_platforms(db: Session, user_id: str) -> list[dict]:
    """Platforms among your audits, with counts (for the filter)."""
    from sqlalchemy import func as sql_func

    rows = (
        db.query(WebsiteAudit.platform, sql_func.count(WebsiteAudit.id))
        .filter(WebsiteAudit.user_id == user_id)
        .group_by(WebsiteAudit.platform)
        .all()
    )
    return sorted(({"platform": p or "Unknown", "count": n} for p, n in rows), key=lambda r: -r["count"])


def _audits_query(
    db: Session, user_id: str, lead_id: str | None, q: str | None, platform: str | None = None, finding_type: str | None = None
):
    query = db.query(WebsiteAudit).filter(WebsiteAudit.user_id == user_id)
    if platform:
        query = query.filter(WebsiteAudit.platform.in_(["", "Unknown", "Other"]) if platform == "Unknown" else WebsiteAudit.platform == platform)
    if finding_type:
        query = query.filter(WebsiteAudit.finding_types.contains([finding_type]))
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
    return query


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


# Free offers that fit an automation-first email (used when you haven't written your own free offers)
AUTOMATION_FREE_OFFERS = [
    "A free automation plan for your business: the 2-3 tasks on your website and front desk that could run on "
    "their own (enquiry replies, bookings, reminders, reviews), and what each would save you each week",
    "A free short screen recording showing how the automation I suggested would work on your own website, "
    "step by step, from the customer's side and yours",
]


def free_offers(settings: UserSettings | None) -> list[str]:
    """The user's free offers (Settings, one per line), or defaults that fit the outreach focus."""
    lines = [line.strip(" -•\t") for line in ((settings.free_offers if settings else "") or "").splitlines()]
    own = [line for line in lines if line]
    if own:
        return own
    focus = (settings.outreach_focus if settings else "") or "balanced"
    if focus == "automation":
        return AUTOMATION_FREE_OFFERS + DEFAULT_FREE_OFFERS[:1]
    if focus == "balanced":
        return DEFAULT_FREE_OFFERS + AUTOMATION_FREE_OFFERS[:1]
    return DEFAULT_FREE_OFFERS


def _adds_signature(db: Session, audit: WebsiteAudit | None) -> bool:
    """The Gmail signature goes under this audit's email unless its campaign switched "Add my signature" off."""
    if audit is None:
        return True
    try:
        from app.services.campaigns import campaign_adds_signature

        return campaign_adds_signature(db, audit)
    except Exception as exc:  # bookkeeping must never block an audit
        logger.warning("campaign_signature_check_failed", audit_id=audit.id, error=str(exc))
        return True


def _signature_expected(db: Session, user_id: str, audit: WebsiteAudit | None) -> bool:
    """When drafting: will the sending Gmail add a signature? (its own one, read from Gmail)"""
    if not _adds_signature(db, audit):
        return False
    if audit is not None and audit.email_account_id:
        account = db.get(EmailAccount, audit.email_account_id)
        return bool(account and account.gmail_signature_html)
    # Rotation: any connected inbox may send it; one without a signature gets the name added at send time
    return any(
        a.gmail_signature_html
        for a in db.query(EmailAccount).filter(EmailAccount.user_id == user_id, EmailAccount.status == "connected")
    )


def _sender_info(db: Session, user_id: str, audit: WebsiteAudit | None = None) -> dict:
    settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    services = [s.name for s in db.query(UserService).filter(UserService.user_id == user_id).all()]
    return {
        "sender_name": settings.sender_name if settings else "",
        "company_name": settings.company_name if settings else "",
        "company_website": settings.company_website if settings else "",
        "company_description": settings.company_description if settings else "",
        "contact_email": settings.contact_email if settings else "",
        "services": services,
        # automation: the email leads with the automation; balanced: website finding + one automation; website
        "outreach_focus": (settings.outreach_focus if settings else "") or "balanced",
        "free_offers": free_offers(settings),
        # The sending Gmail's own signature (name, title, links) is added under the email when it is sent.
        "signature_added": _signature_expected(db, user_id, audit),
    }


def _lead_info(lead: Lead) -> dict:
    return {
        "business_name": lead.business_name,
        "contact_name": lead.contact_name,
        # Who the email greets: the owner's / recipient's first name, or "" for "Hi there,"
        "greeting_name": owner_first_name(lead),
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
        "email_account_id": audit.email_account_id,  # sends from this account (none: the default account)
        "platform": audit.platform or (audit.site_data or {}).get("platform") or "",  # Shopify, WordPress...
        "platform_confidence": audit.platform_confidence,
        "platform_confidence_label": ((audit.site_data or {}).get("platform_intel") or {}).get("confidence_label", ""),
        "finding_types": audit.finding_types or [],
        "outreach_angle": audit.outreach_angle or (audit.report or {}).get("outreach_angle", ""),
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
                "platform_intel": (audit.site_data or {}).get("platform_intel") or {},
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
        if email_account_id:
            email_account_id = resolve_email_account(db, user_id, email_account_id).id
        else:
            # No inbox chosen: none is fixed now; rotation picks one when the email is sent (just check one exists)
            resolve_email_account(db, user_id, None)
            email_account_id = None
    elif email_account_id:
        email_account_id = resolve_email_account(db, user_id, email_account_id, require_connected=False).id

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
        # What the pages show that decides which automation to propose (services/automations.py)
        "automation_signals": website_signals([p.html for p in crawl.pages if p.html], detection.secondary),
        "social_links": crawl.social,
        "emails_found": [e["email"] for e in crawl.emails][:5],
        "business_summary": analysis.get("business_summary", ""),
        "services": analysis.get("services", [])[:10],
        "rule_based_issues": analysis.get("issues", []),
        "rule_based_opportunities": analysis.get("opportunities", []),
        "rule_based_scores": analysis.get("category_scores", {}),
        "rule_based_website_score": analysis.get("website_score"),
        "text_excerpt": text[:3000],
        "platform_intel": platform_intelligence(
            url, [(p.url, p.html) for p in crawl.pages if p.html], detection, homepage.status_code if homepage else None
        ),
    }


def _automation_offers(db: Session, user_id: str) -> str | None:
    row = db.query(UserSettings.automation_offers).filter(UserSettings.user_id == user_id).first()
    return row[0] if row else None


def keep_offered_automations(items: list[dict], options: list[dict]) -> list[dict]:
    """Only automations that were offered to the analysis (so only ones you sell); "question" ones stay questions."""
    allowed = {o["key"]: o for o in options}
    kept = []
    for item in items:
        option = allowed.get(item.get("key"))
        if not option or any(k["key"] == option["key"] for k in kept):
            continue
        kept.append({**item, "name": option["name"], "basis": option["basis"]})
    return kept[:3]


def _redetect_platform(site: dict, url: str, page: dict, log) -> dict:
    """The plain fetch couldn't tell the platform (e.g. a firewall served a challenge page): detect it again from the
    page the real browser rendered, and keep that when it is more certain."""
    if (site.get("platform_confidence") or 0) >= 0.5 and site.get("platform") not in ("", "Unknown"):
        return site
    detection = detect_platform(html=page.get("html") or "", headers=page.get("headers") or {}, url=url)
    if detection.platform_name == "Unknown" or detection.confidence <= (site.get("platform_confidence") or 0):
        return site
    log.info("platform_redetected_from_browser", platform=detection.platform_name, confidence=detection.confidence)
    return site | {
        "automation_signals": merge_signals(
            site.get("automation_signals"), website_signals([page.get("html") or ""], detection.secondary)
        ),
        "platform": detection.platform_name,
        "platform_confidence": detection.confidence,
        "technologies": [t["name"] for t in detection.technologies][:15],
        "platform_intel": platform_intelligence(url, [(url, page.get("html") or "")], detection, page.get("status") or 200),
    }


def platform_intelligence(url: str, pages: list[tuple[str, str]], detection, home_status: int | None = None) -> dict:
    """Detected platform (with confidence, add-ons and evidence) and the evidence-based feature checks.
    Never fails the audit: a problem is recorded and the audit carries on."""
    from app.services.platform_checks import run_platform_checks
    from app.services.platform_detection import platform_summary

    intel = platform_summary(detection, datetime.now(UTC).isoformat())
    try:
        intel.update(run_platform_checks(url, pages, detection, home_status))
        if intel.get("unreadable"):
            # A block / error page says nothing about the platform either
            intel.update({"primary": "Unknown", "confidence": 0.0, "confidence_label": "Unknown"})
    except Exception as exc:
        logger.warning("platform_intel_failed", url=url, error=str(exc)[:200])
        intel.update({"checks": [], "counts": {}, "error": str(exc)[:200]})
    return intel


def _report_for_email(audit: WebsiteAudit, report: dict) -> dict:
    """The report plus the platform and your (possibly edited) outreach angle, for writing the email."""
    return {**report, "platform": audit.platform, "outreach_angle": audit.outreach_angle or report.get("outreach_angle", "")}


def set_outreach_angle(db: Session, user_id: str, audit_id: str, text: str) -> WebsiteAudit:
    audit = get_audit(db, user_id, audit_id)
    audit.outreach_angle = (text or "").strip()[:1500]
    db.commit()
    db.refresh(audit)
    return audit


def apply_platform_results(audit: WebsiteAudit, lead: Lead | None, report: dict) -> None:
    """Keep the report honest against the checks, then store platform, finding types and the outreach angle."""
    intel = (audit.site_data or {}).get("platform_intel") or {}
    checks = intel.get("checks") or []
    known_urls = {e.get("url", "").rstrip("/") for c in checks for e in c.get("evidence") or []}
    known_urls |= {(p.get("url") or "").rstrip("/") for p in (audit.site_data or {}).get("pages_crawled") or []}
    known_urls.add((audit.website_url or "").rstrip("/"))
    present = {c["feature"].lower() for c in checks if c.get("status") == "present"}
    for item in [*report.get("findings", []), *report.get("platform_opportunities", [])]:
        # "Missing" needs evidence from a page the audit actually looked at; otherwise a human should confirm
        if item.get("status") == "confirmed_missing":
            url = (item.get("evidence_url") or "").rstrip("/")
            if url and url not in known_urls and not item.get("where"):
                item["status"] = "manual_review"
            if any(p in (item.get("title") or "").lower() for p in present if len(p) > 6):
                item["status"] = "manual_review"  # a check saw it present: don't call it missing
    types = sorted(
        {i.get("status") for i in [*report.get("findings", []), *report.get("platform_opportunities", [])] if i.get("status")}
        | {c["status"] for c in checks if c.get("status") != "present"}
    )
    audit.platform = (intel.get("primary") or (audit.site_data or {}).get("platform") or "")[:64]
    audit.platform_confidence = intel.get("confidence")
    audit.finding_types = types
    if not audit.outreach_angle:
        audit.outreach_angle = report.get("outreach_angle") or ""
    if lead is not None and audit.platform and audit.platform != "Unknown":
        lead.platform = audit.platform[:64]  # lead lists and filters show it too


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


PDF_VERSION = 4  # 2: the "Your Website Platform" section; 3: layout fixes; 4: cover name block for long names

# Add-ons worth naming to the business owner (not analytics / CDN plumbing)
PDF_ADDON_CATEGORIES = ("platform", "page_builder", "forms", "booking", "reviews", "email", "performance",
                        "seo", "chat", "recommendations", "subscriptions", "framework")


def pdf_platform(audit: WebsiteAudit) -> dict:
    """What the PDF says about the platform: its name, notable add-ons and the agency that built it."""
    site = audit.site_data or {}
    intel = site.get("platform_intel") or {}
    secondary = intel.get("secondary") or []
    addons, built_by = [], ""
    for item in secondary:
        if item.get("category") == "agency":
            built_by = (item.get("name") or "").removeprefix("Built by ").strip()
        elif item.get("category") in PDF_ADDON_CATEGORIES and item.get("name") not in addons:
            addons.append(item["name"])
    return {
        "name": audit.platform or intel.get("primary") or site.get("platform") or "",
        "confidence_label": intel.get("confidence_label") or "",
        "addons": addons[:5],
        "built_by": built_by,
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
        platform=pdf_platform(audit),
        automation_first=sender.get("outreach_focus") == "automation",
    )
    audit.pdf_data = pdf
    audit.site_data = {**(audit.site_data or {}), "pdf_version": PDF_VERSION}
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
        _discover_contacts(db, audit, lead, log)

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
        browser_page = screenshots.pop("_page", None)
        if browser_page:
            site = _redetect_platform(site, audit.website_url, browser_page, log)
            # anything the real browser saw (booking, chat, review widgets loaded by scripts) counts too
            site["automation_signals"] = merge_signals(
                site.get("automation_signals"), website_signals([browser_page.get("html") or ""])
            )
        audit.site_data = site | {"screenshots": {k: len(v) for k, v in screenshots.items()}}
        db.commit()
        images = screenshots.get("desktop", []) + screenshots.get("mobile", [])

        current = "analyzing"
        _set_step(db, audit, "analyzing")
        config = resolve_ai_config(db, audit.user_id)
        provider = get_ai_provider_for_user(db, audit.user_id)
        audit.ai_provider, audit.ai_model = config.provider, config.model
        portfolio_sites = portfolio_for_prompt(db, audit.user_id)
        # Automations worth proposing: from what the site shows and the business type, only ones you offer
        automation_options = automation_candidates(
            site.get("automation_signals"), lead.category or "", parse_offers(_automation_offers(db, audit.user_id))
        )
        report = generate_audit_report(
            provider,
            lead=lead_info,
            site={k: v for k, v in site.items() if k != "text_excerpt"}
            | {"text_excerpt": site["text_excerpt"][:1500], "automation_candidates": automation_options},
            performance=performance,
            timeout=settings.audit_ai_timeout_seconds,
            images=images,
            free_offers=_sender_info(db, audit.user_id)["free_offers"],
            outreach_focus=_sender_info(db, audit.user_id)["outreach_focus"],
            portfolio=portfolio_sites,
        )
        report["reference_sites"] = keep_known(report.get("reference_sites") or [], portfolio_sites)
        report["automation_opportunities"] = keep_offered_automations(
            report.get("automation_opportunities") or [], automation_options
        )
        apply_platform_results(audit, lead, report)
        audit.report = report
        audit.overall_score = report.get("overall_score")
        db.commit()

        current = "generating_pdf"
        _set_step(db, audit, "generating_pdf")
        sender = _sender_info(db, audit.user_id, audit)
        _build_pdf(audit, lead, sender, screenshots)
        db.commit()

        current = "drafting_email"
        _set_step(db, audit, "drafting_email")
        email = generate_audit_email(
            provider,
            lead=lead_info,
            report=_report_for_email(audit, report),
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
                # Sent automatically by the poller once allowed: daily limit, sending hours or a paused inbox
                # (see auto_audits.send_waiting).
                from app.services.deliverability import waiting_step

                audit.step = waiting_step(exc, WAITING_FOR_LIMIT)
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
        sender = _sender_info(db, audit.user_id, audit)
        if not audit.pdf_data:
            _build_pdf(audit, lead, sender, _saved_screenshots(audit.id))
        email = generate_audit_email(
            get_ai_provider_for_user(db, audit.user_id),
            lead=_lead_info(lead) | {"website_url": audit.website_url},
            report=_report_for_email(audit, audit.report or {}),
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


def _origin_extra(db: Session, audit: WebsiteAudit) -> dict:
    """extra of the campaign / scrape file this audit belongs to (its recipient options live there)."""
    campaign = _audit_campaign(db, audit)
    if campaign:
        return campaign.extra or {}
    if audit.trigger == "auto":
        from app.models import ScrapingJob

        lead = db.get(Lead, audit.lead_id)
        job = db.get(ScrapingJob, lead.scraping_job_id) if lead and lead.scraping_job_id else None
        return (job.extra or {}) if job else {}
    return {}


def _discover_contacts(db: Session, audit: WebsiteAudit, lead: Lead, log) -> None:
    """Find the business's other contacts (team / about / contact / imprint pages) and pick the best primary
    recipient. The audit's recipient follows the new choice unless you typed a different address yourself."""
    from app.services.contacts import discover

    before = (lead.primary_email or "").lower()
    try:
        discover(db, lead)
    except Exception as exc:  # discovery must never fail an audit
        db.rollback()
        log.warning("contact_discovery_failed", error=str(exc)[:200])
        return
    db.refresh(lead)
    if (audit.recipient_email or "").lower() in ("", before) and lead.primary_email:
        audit.recipient_email = lead.primary_email
        db.commit()


def _audit_campaign(db: Session, audit: WebsiteAudit):
    if audit.trigger != "campaign":
        return None
    from app.services.campaigns import campaign_for_audit

    try:
        return campaign_for_audit(db, audit)
    except Exception:
        return None


def campaign_rotation(campaign) -> dict:
    from app.services.campaigns import campaign_rotation as rotation

    return rotation(campaign)


def audit_file_rotation(db: Session, audit: WebsiteAudit) -> dict:
    """The scrape file's rotation for this audit's email ({"mode": "auto"} when it has none)."""
    default = {"mode": "auto", "account_ids": []}
    if audit.trigger != "auto" or not audit.lead_id:
        return default
    try:
        from app.models import ScrapingJob

        lead = db.get(Lead, audit.lead_id)
        job = db.get(ScrapingJob, lead.scraping_job_id) if lead and lead.scraping_job_id else None
        rotation = (job.extra or {}).get("rotation") if job else None
    except Exception as exc:  # bookkeeping must never block a send
        logger.warning("rotation_lookup_failed", audit_id=audit.id, error=str(exc))
        return default
    if not rotation or rotation.get("mode") in (None, "single"):
        return default
    return {"mode": rotation["mode"], "account_ids": list(rotation.get("account_ids") or []) if rotation["mode"] == "selected" else []}


def audit_schedule(db: Session, audit: WebsiteAudit) -> dict | None:
    """The sending schedule of the scrape file / campaign this audit belongs to (None: your sending hours)."""
    try:
        if audit.trigger == "campaign":
            from app.services.campaigns import campaign_for_audit

            campaign = campaign_for_audit(db, audit)
            return (campaign.extra or {}).get("schedule") if campaign else None
        if audit.trigger == "auto":
            from app.models import ScrapingJob

            lead = db.get(Lead, audit.lead_id)
            job = db.get(ScrapingJob, lead.scraping_job_id) if lead and lead.scraping_job_id else None
            return (job.extra or {}).get("schedule") if job else None
    except Exception as exc:  # bookkeeping must never block a send
        logger.warning("schedule_lookup_failed", audit_id=audit.id, error=str(exc))
    return None


def _followups_for(db: Session, audit: WebsiteAudit, settings: UserSettings | None) -> bool:
    """Follow-ups after this audit email: the setting, and the campaign's / scrape file's own switch."""
    if settings is not None and not settings.followups_enabled:
        return False
    try:
        if audit.trigger == "campaign":
            from app.services.campaigns import campaign_followups, campaign_for_audit

            return campaign_followups(campaign_for_audit(db, audit))
        if audit.trigger == "auto":
            from app.models import ScrapingJob

            lead = db.get(Lead, audit.lead_id)
            job = db.get(ScrapingJob, lead.scraping_job_id) if lead and lead.scraping_job_id else None
            return not job or (job.extra or {}).get("followups", True) is not False
    except Exception as exc:  # bookkeeping must never block a send
        logger.warning("followup_setting_check_failed", audit_id=audit.id, error=str(exc))
    return True


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
    manual: bool = False,
) -> WebsiteAudit:
    """Send the audit email with its PDF. Automatic sends (manual=False) wait for the sending hours."""
    from app.services import deliverability
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
    # Who receives it: the best contact at this business (and CC'd colleagues when the campaign / file allows),
    # unless you typed a different address yourself
    options = recipient_options(_origin_extra(db, audit))
    cc: list[str] = []
    if lead and lead.emails and recipient_email is None:
        choice = select_recipients(
            db, lead, include_generic=options["include_generic"], cc_enabled=options["cc_enabled"], cc_max=options["cc_max"]
        )
        typed_by_you = audit.recipient_email and audit.recipient_email.lower() not in {e.email.lower() for e in lead.emails}
        if not typed_by_you:
            recipient = _valid_email(choice["to"]) or ("" if not options["include_generic"] else recipient)
        cc = [c for c in choice["cc"] if c != (recipient or "").lower()]
    if lead and not manual:
        # One campaign message per company: never a second copy through another address of the same business
        campaign_for_dedup = _audit_campaign(db, audit)
        skip_previous = campaign_for_dedup.skip_previously_contacted if campaign_for_dedup else True
        already = company_already_contacted(db, lead, campaign_for_dedup.id if campaign_for_dedup else None, skip_previous)
        if already and audit.status != "sent":
            audit.auto_send = False
            audit.step = f"Not sent: {already}"
            db.commit()
            raise HTTPException(status_code=409, detail=already)
    if not recipient:
        raise HTTPException(status_code=400, detail="Add the customer's email address first.")
    if not subject or not (body_html or "").strip():
        raise HTTPException(status_code=400, detail="The email subject and body are required.")
    if audit.pdf_data and lead and (audit.site_data or {}).get("pdf_version", 1) < PDF_VERSION:
        # Made before the current PDF layout (e.g. without "Your Website Platform"): rebuilt from the stored audit,
        # no new analysis (no Claude tokens)
        try:
            _build_pdf(audit, lead, _sender_info(db, user_id, audit), _saved_screenshots(audit.id))
            db.commit()
        except Exception as exc:  # the earlier PDF still goes out
            db.rollback()
            logger.warning("pdf_rebuild_failed", audit_id=audit.id, error=str(exc)[:200])
    pdf = audit.pdf_data
    if not pdf:
        raise HTTPException(status_code=400, detail="The PDF audit is missing. Run the audit again.")
    if lead and lead.do_not_contact:
        raise HTTPException(status_code=400, detail="This lead is marked do not contact.")
    settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    if (not settings or settings.respect_suppression_list) and is_suppressed(db, user_id, recipient):
        raise HTTPException(status_code=400, detail="This address is on your suppression list (unsubscribed).")

    from app.services.email_verification import verification_problem

    # Checked again right before sending (the address may have been chosen before today's rules, or by hand):
    # never send to an address that would bounce or a risky type that isn't confirmed
    from app.services import address_check

    problem = deliverability.address_problem(db, user_id, recipient)
    # The local risk check (format, sending history, domain DNS), fail closed: only a fresh low-risk result sends.
    # It is not mailbox verification; the verification service (if set up) still runs after it.
    local = address_check.check(db, user_id, recipient)
    if not problem and local.retry_later:
        if manual:
            raise HTTPException(status_code=400, detail=local.reason)
        # DNS didn't answer: waits, like the sending hours, and is checked again on the next round
        raise deliverability.SendDeferred(local.reason, f"Waiting: {local.reason}")
    if not problem and not local.low_risk:
        problem = local.reason
    problem = problem or (verification_problem(db, user_id, lead, recipient) if lead else "")
    if problem:
        if not manual:
            audit.auto_send = False  # held for you: choose another address on the lead, then send
            audit.step = (
                "Held: the local risk check couldn't clear this address (choose another on the lead)"
                if problem == local.reason and local.status == address_check.HELD
                else "Not sent: the address may bounce (choose another on the lead)"
            )
            audit.error_message = problem[:500]
            db.commit()
        raise HTTPException(status_code=400, detail=problem)
    cc = address_check.low_risk_only(db, user_id, cc)  # CC'd colleagues too: one that doesn't pass is left out
    if not manual:
        # The file's / campaign's own schedule in the lead's time zone, else your sending hours
        deliverability.require_send_time(db, user_id, audit_schedule(db, audit), lead)
    # The chosen inbox, or (none chosen / "" = automatic) the eligible inbox with the most room today,
    # among the accounts its campaign may use (rotation mode / chosen accounts)
    campaign = _audit_campaign(db, audit)
    rotation = campaign_rotation(campaign) if campaign else audit_file_rotation(db, audit)
    account: EmailAccount = deliverability.sending_account(
        db,
        user_id,
        (email_account_id if email_account_id is not None else audit.email_account_id) or None,
        automatic=not manual,
        allowed_ids=rotation["account_ids"] or None,
        mode=rotation["mode"],
    )
    # Account health protection, immediately before sending: health, Gmail restrictions, safe capacity, your limit
    # (in that order), with the inbox locked until this email's record is committed below (atomic reservation).
    from app.services import send_protection

    try:
        send_protection.guard(db, account, spacing=not manual)  # spacing avoids Gmail's "421 server busy"
    except send_protection.HealthHold as hold:
        if manual and hold.verdict.safety_block:
            raise HTTPException(
                status_code=400,
                detail=f"{send_protection.WAITING_FOR_HEALTH}: {hold.verdict.reason} Choose another inbox, or wait "
                "until the warning clears (see Email health).",
            ) from hold
        raise send_protection.as_deferred(hold) from hold  # a full inbox: held until its limit resets, as before

    unsubscribe = unsubscribe_url(user_id, recipient)
    rfc_message_id = new_rfc_message_id(account.email_address)
    # This Gmail's own signature (read from Gmail; never another account's)
    sig_html, sig_text = account_signature(account) if _adds_signature(db, audit) else ("", "")
    name, company = (settings.sender_name, settings.company_name) if settings else ("", "")
    # "Hi <owner's first name>," or "Hi there," (never the business name), also for drafts written earlier
    body_html = apply_greeting(body_html, owner_first_name(lead, recipient))
    if sig_html:
        sent_body = drop_ai_sign_off(body_html, name, company)  # the signature carries the name / company
    else:
        sent_body = ensure_sign_off(body_html, name, company)  # no signature: a bare "Best," gets the name
    signed_html, signed_text = append_signature(sent_body, _html_to_text(sent_body), sig_html, sig_text)
    html_body = (
        f"{signed_html}"
        f'<p style="font-size:12px;color:#666">Not interested? <a href="{unsubscribe}">Unsubscribe</a></p>'
    )
    text_body = f"{signed_text}\n\nUnsubscribe: {unsubscribe}"
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
        followups_enabled=_followups_for(db, audit, settings),
    )
    # One send at a time per audit: claim it atomically, so a retry, the poller and a click can't send it twice
    claimed = db.execute(
        update(WebsiteAudit)
        .where(WebsiteAudit.id == audit.id, WebsiteAudit.status.in_(list(SENDABLE_STATUSES)))
        .values(status="sending", step=STEP_LABELS["sending"], error_message="")
    ).rowcount
    if not claimed:
        db.rollback()
        raise HTTPException(status_code=409, detail="This email is already being sent.")
    message.campaign_id = campaign.id if campaign else None  # per-account campaign report
    message.cc_emails = cc
    db.add(message)
    db.refresh(audit)
    audit.email_subject, audit.email_html, audit.recipient_email = subject, body_html, recipient
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
            cc=cc,
        )
    except GmailSendError as exc:
        message.status, message.error_message, message.error_kind = "failed", str(exc), exc.kind
        audit.status, audit.step, audit.error_message = "send_failed", STEP_LABELS["send_failed"], str(exc)
        audit.send_retryable = exc.retryable  # permanent failures are never retried automatically
        db.commit()
        if exc.kind == "recipient":
            from app.services.account_health import suppress_refused_recipient

            suppress_refused_recipient(db, user_id, audit.lead_id, recipient)
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
