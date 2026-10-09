from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_config import get_logger
from app.models import Lead, LeadEmail, User, UserSettings, Website, WebsiteAnalysis, WebsiteTechnology
from app.services.ai import AINotConfiguredError, enhance_analysis
from app.services.analysis import analyze_website_signals
from app.services.crawler import crawl_website
from app.services.platform_detection import detect_platform
from app.services.scoring import calculate_lead_score
from app.utils.emails import choose_primary_email
from app.utils.normalize import extract_domain, normalize_url

logger = get_logger("website_pipeline")


def _mark_lead_status(db: Session, lead: Lead, status: str) -> None:
    lead.status = status
    db.add(lead)
    db.commit()
    from app.services.lead_pipeline import refresh_import_batch

    refresh_import_batch(db, lead.import_batch_id)


def process_lead_website(db: Session, lead_id: str) -> Website | None:
    lead = db.get(Lead, lead_id)
    if not lead:
        return None
    lead.status = "processing"
    db.add(lead)
    db.commit()
    if not lead.website_url:
        from app.services.leads import discard_lead_without_email, outreach_status

        if discard_lead_without_email(db, lead):
            return None
        _mark_lead_status(db, lead, outreach_status(lead.primary_email))
        return None
    settings = db.query(UserSettings).filter(UserSettings.user_id == lead.user_id).first()
    url = normalize_url(lead.website_url)
    domain = extract_domain(url)
    website = lead.website or Website(user_id=lead.user_id, lead_id=lead.id, url=url, domain=domain)
    website.url = url
    website.domain = domain
    website.crawl_status = "running"
    db.add(website)
    db.commit()
    db.refresh(website)

    app_settings = get_settings()
    result = crawl_website(
        url,
        max_pages=1,
        timeout=min(settings.crawl_timeout_seconds if settings else app_settings.crawl_timeout_seconds, 12),
    )
    homepage = result.homepage
    website.reachable = bool(homepage and homepage.status_code and homepage.status_code < 400)
    website.https = url.startswith("https://")
    website.title = homepage.title if homepage else ""
    website.meta_description = homepage.meta_description if homepage else ""
    website.extracted_text = " ".join(p.text for p in result.pages)[:20000]
    website.social_links = result.social
    website.raw_headers = result.headers
    website.pages_crawled = len(result.pages)
    website.last_crawled_at = datetime.now(UTC)
    website.crawl_status = "failed" if result.error and not website.reachable else "completed"

    html = homepage.html if homepage else ""
    detection = detect_platform(html=html, headers=result.headers, url=url)
    website.platform_name = detection.platform_name
    website.platform_confidence = detection.confidence
    lead.platform = detection.platform_name

    db.query(WebsiteTechnology).filter(WebsiteTechnology.website_id == website.id).delete()
    for tech in detection.technologies:
        db.add(
            WebsiteTechnology(
                website_id=website.id,
                name=tech["name"],
                confidence=tech["confidence"],
                signals=tech["signals"],
                is_primary_platform=tech["is_primary_platform"],
            )
        )

    if result.emails:
        existing = {e.email for e in lead.emails}
        for item in result.emails:
            if item["email"] not in existing:
                db.add(
                    LeadEmail(
                        lead_id=lead.id,
                        email=item["email"],
                        is_primary=False,
                        source_url=item.get("source_url") or "",
                    )
                )
                existing.add(item["email"])
        all_emails = list(existing)
        primary = choose_primary_email(all_emails, domain)
        if primary:
            lead.primary_email = primary
            lead.email_status = "found"
            for email in lead.emails:
                email.is_primary = email.email == primary

    pages_meta = [{"url": p.url, "status_code": p.status_code} for p in result.pages]
    analysis_data = analyze_website_signals(
        text=website.extracted_text,
        title=website.title,
        meta_description=website.meta_description,
        signals=result.signals,
        pages=pages_meta,
        platform=website.platform_name,
        city=lead.city,
        category=lead.category,
        business_name=lead.business_name,
    )
    analysis_data["summary"] = analysis_data.get("business_summary") or f"Website platform: {website.platform_name or 'Unknown'}"

    analysis = website.analysis or WebsiteAnalysis(website_id=website.id)
    analysis.status = "completed"
    analysis.raw_data = analysis_data["raw_data"]
    analysis.summary = analysis_data["summary"]
    analysis.business_summary = analysis_data["business_summary"]
    analysis.business_type = analysis_data["business_type"]
    analysis.services = analysis_data["services"]
    analysis.products = analysis_data["products"]
    analysis.main_offering = analysis_data["main_offering"]
    analysis.target_audience = analysis_data["target_audience"]
    analysis.location = analysis_data["location"]
    analysis.issues = analysis_data["issues"]
    analysis.opportunities = analysis_data["opportunities"]
    analysis.recommended_services = analysis_data["recommended_services"]
    analysis.category_scores = analysis_data["category_scores"]
    analysis.website_score = analysis_data["website_score"]
    analysis.analyzed_at = datetime.now(UTC)
    analysis.error_message = result.error or ""
    db.add(analysis)

    website.last_analyzed_at = datetime.now(UTC)
    lead.website_score = analysis.website_score
    score, reasons = calculate_lead_score(lead, {"issues": analysis.issues})
    lead.lead_score = score
    lead.lead_score_reasons = reasons
    db.add(website)
    db.add(lead)
    db.commit()
    db.refresh(website)
    from app.services.leads import discard_lead_without_email, outreach_status

    if discard_lead_without_email(db, lead):
        return website
    _mark_lead_status(db, lead, outreach_status(lead.primary_email))
    if (lead.primary_email or "").strip() and lead.website_url:
        # Personalised outreach: audit -> PDF -> Claude email -> send (picked up by the automatic audit poller).
        from app.services.auto_audits import request_poll

        request_poll()
    logger.info("website_processed", lead_id=lead.id, website_id=website.id, platform=website.platform_name)
    return website
