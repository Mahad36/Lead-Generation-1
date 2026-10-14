"""Add platform intelligence to audits made before it existed, in the background and gently.

One audit at a time with a pause in between (each one re-reads a few public pages of the site and, optionally,
asks Claude for the platform opportunities + outreach angle). Existing findings, PDFs and emails are kept; only
the platform fields are added. Started from the Website audits page, a batch at a time (default 25).
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

from app.logging_config import get_logger

logger = get_logger("platform_reprocess")

PAUSE_SECONDS = 3
_state: dict[str, dict] = {}
_lock = threading.Lock()


def status(user_id: str) -> dict:
    from app.database import get_session
    from app.models import WebsiteAudit

    db = get_session()
    try:
        remaining = sum(
            1
            for (site,) in db.query(WebsiteAudit.site_data).filter(
                WebsiteAudit.user_id == user_id, WebsiteAudit.status.in_(["ready", "sent", "send_failed"])
            )
            if not (site or {}).get("platform_intel")
        )
    finally:
        db.close()
    with _lock:
        state = dict(_state.get(user_id) or {"running": False})
    return {**state, "older_audits_without_platform": remaining}


def start(user_id: str, limit: int = 25, with_ai: bool = True) -> dict:
    with _lock:
        if (_state.get(user_id) or {}).get("running"):
            return {**_state[user_id], "message": "Already running"}
        _state[user_id] = {"running": True, "done": 0, "failed": 0, "total": 0, "started_at": datetime.now(UTC).isoformat()}
    threading.Thread(target=_run, args=(user_id, max(1, min(limit, 200)), with_ai), name="platform-reprocess", daemon=True).start()
    return status(user_id)


def _run(user_id: str, limit: int, with_ai: bool) -> None:
    from app.config import get_settings
    from app.database import get_session
    from app.models import Lead, WebsiteAudit
    from app.services.ai import get_ai_provider_for_user
    from app.services.audit_ai import generate_platform_addendum
    from app.services.crawler import crawl_website
    from app.services.platform_detection import detect_platform
    from app.services.website_audit import _lead_info, apply_platform_results, platform_intelligence

    db = get_session()
    try:
        audits = [
            a
            for a in db.query(WebsiteAudit)
            .filter(WebsiteAudit.user_id == user_id, WebsiteAudit.status.in_(["ready", "sent", "send_failed"]))
            .order_by(WebsiteAudit.created_at.desc())
            if not (a.site_data or {}).get("platform_intel")
        ][:limit]
        with _lock:
            _state[user_id]["total"] = len(audits)
        provider = None
        if with_ai:
            try:
                provider = get_ai_provider_for_user(db, user_id)
            except Exception as exc:
                logger.info("reprocess_without_ai", error=str(exc)[:160])
        for audit in audits:
            with _lock:
                _state[user_id]["current"] = audit.website_url
            try:
                crawl = crawl_website(audit.website_url, max_pages=3, timeout=15)
                html = crawl.homepage.html if crawl.homepage else ""
                detection = detect_platform(html=html, headers=crawl.headers, url=audit.website_url)
                intel = platform_intelligence(
                    audit.website_url,
                    [(p.url, p.html) for p in crawl.pages if p.html],
                    detection,
                    crawl.homepage.status_code if crawl.homepage else None,
                )
                if intel.get("unreadable") and (audit.site_data or {}).get("platform") not in (None, "", "Unknown", "Other"):
                    # Keep what the original audit saw rather than overwriting it with "Unknown"
                    intel["primary"] = audit.site_data["platform"]
                    intel["confidence_label"] = "Low"
                site = dict(audit.site_data or {})
                site.update({"platform_intel": intel})
                audit.site_data = site
                report = dict(audit.report or {})
                lead = db.get(Lead, audit.lead_id)
                if provider and report.get("findings") and lead:
                    report.update(
                        generate_platform_addendum(
                            provider,
                            lead=_lead_info(lead),
                            report=report,
                            platform_intel=intel,
                            timeout=min(get_settings().audit_ai_timeout_seconds, 240),
                        )
                    )
                apply_platform_results(audit, lead, report)
                audit.report = report
                db.commit()
                with _lock:
                    _state[user_id]["done"] += 1
            except Exception as exc:  # one site failing doesn't stop the batch
                db.rollback()
                logger.warning("reprocess_failed", audit_id=audit.id, error=str(exc)[:200])
                with _lock:
                    _state[user_id]["failed"] += 1
            time.sleep(PAUSE_SECONDS)
    finally:
        db.close()
        with _lock:
            _state[user_id].update({"running": False, "current": "", "finished_at": datetime.now(UTC).isoformat()})
