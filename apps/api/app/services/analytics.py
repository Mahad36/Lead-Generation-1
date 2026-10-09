from datetime import UTC, datetime, timedelta

from sqlalchemy import cast, Date, func
from sqlalchemy.orm import Session

from app.models import Campaign, CampaignLead, EmailMessage, Lead, SuppressionEntry, Website


def dashboard_stats(db: Session, user_id: str) -> dict:
    today = datetime.now(UTC).date()
    total_leads = db.query(func.count(Lead.id)).filter(Lead.user_id == user_id).scalar() or 0
    with_email = (
        db.query(func.count(Lead.id)).filter(Lead.user_id == user_id, Lead.primary_email != "").scalar() or 0
    )
    analyzed = (
        db.query(func.count(Website.id))
        .filter(Website.user_id == user_id, Website.last_analyzed_at.is_not(None))
        .scalar()
        or 0
    )
    campaigns = db.query(func.count(Campaign.id)).filter(Campaign.user_id == user_id).scalar() or 0
    sent_today = (
        db.query(func.count(EmailMessage.id))
        .filter(
            EmailMessage.user_id == user_id,
            EmailMessage.status == "sent",
            cast(EmailMessage.sent_at, Date) == today,
        )
        .scalar()
        or 0
    )
    failed = (
        db.query(func.count(EmailMessage.id))
        .filter(EmailMessage.user_id == user_id, EmailMessage.status == "failed")
        .scalar()
        or 0
    )
    bounced = (
        db.query(func.count(EmailMessage.id))
        .filter(EmailMessage.user_id == user_id, EmailMessage.status == "bounced")
        .scalar()
        or 0
    )
    unsub = (
        db.query(func.count(SuppressionEntry.id))
        .filter(SuppressionEntry.user_id == user_id, SuppressionEntry.reason == "user_unsubscribed")
        .scalar()
        or 0
    )
    from app.models import UserSettings

    settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    daily_limit = settings.default_daily_send_limit if settings else 50
    platforms = (
        db.query(Lead.platform, func.count(Lead.id))
        .filter(Lead.user_id == user_id, Lead.platform != "")
        .group_by(Lead.platform)
        .all()
    )
    by_country = (
        db.query(Lead.country, func.count(Lead.id))
        .filter(Lead.user_id == user_id, Lead.country != "")
        .group_by(Lead.country)
        .all()
    )
    by_category = (
        db.query(Lead.category, func.count(Lead.id))
        .filter(Lead.user_id == user_id, Lead.category != "")
        .group_by(Lead.category)
        .all()
    )
    avg_website = (
        db.query(func.avg(Lead.website_score)).filter(Lead.user_id == user_id, Lead.website_score.is_not(None)).scalar()
    )
    avg_lead = (
        db.query(func.avg(Lead.lead_score)).filter(Lead.user_id == user_id, Lead.lead_score.is_not(None)).scalar()
    )
    active_campaigns = (
        db.query(func.count(Campaign.id)).filter(Campaign.user_id == user_id, Campaign.status == "running").scalar() or 0
    )
    completed_campaigns = (
        db.query(func.count(Campaign.id)).filter(Campaign.user_id == user_id, Campaign.status == "completed").scalar()
        or 0
    )
    suppressed = db.query(func.count(SuppressionEntry.id)).filter(SuppressionEntry.user_id == user_id).scalar() or 0
    return {
        "total_leads": total_leads,
        "leads_with_email": with_email,
        "leads_without_email": total_leads - with_email,
        "websites_analyzed": analyzed,
        "valid_emails": with_email,
        "campaigns": campaigns,
        "active_campaigns": active_campaigns,
        "completed_campaigns": completed_campaigns,
        "emails_sent_today": sent_today,
        "remaining_daily_capacity": max(daily_limit - sent_today, 0) if daily_limit else None,
        "average_website_score": round(float(avg_website), 1) if avg_website is not None else None,
        "average_lead_score": round(float(avg_lead), 1) if avg_lead is not None else None,
        "open_rate": None,
        "bounce_count": bounced,
        "unsubscribe_count": unsub,
        "suppressed_contacts": suppressed,
        "failed_emails": failed,
        "platform_distribution": [{"platform": p or "unknown", "count": c} for p, c in platforms],
        "leads_by_country": [{"country": c or "unknown", "count": n} for c, n in by_country],
        "leads_by_category": [{"category": c or "unknown", "count": n} for c, n in by_category],
    }


def timeseries(db: Session, user_id: str, days: int = 30) -> dict:
    start = datetime.now(UTC) - timedelta(days=days)
    leads = (
        db.query(cast(Lead.created_at, Date), func.count(Lead.id))
        .filter(Lead.user_id == user_id, Lead.created_at >= start)
        .group_by(cast(Lead.created_at, Date))
        .order_by(cast(Lead.created_at, Date))
        .all()
    )
    emails = (
        db.query(cast(EmailMessage.sent_at, Date), func.count(EmailMessage.id))
        .filter(
            EmailMessage.user_id == user_id,
            EmailMessage.status == "sent",
            EmailMessage.sent_at >= start,
        )
        .group_by(cast(EmailMessage.sent_at, Date))
        .order_by(cast(EmailMessage.sent_at, Date))
        .all()
    )
    campaigns = (
        db.query(Campaign.id, Campaign.name, Campaign.status, func.count(CampaignLead.id))
        .outerjoin(CampaignLead, CampaignLead.campaign_id == Campaign.id)
        .filter(Campaign.user_id == user_id)
        .group_by(Campaign.id)
        .all()
    )
    return {
        "leads_over_time": [{"date": str(d), "count": c} for d, c in leads],
        "emails_over_time": [{"date": str(d), "count": c} for d, c in emails],
        "campaign_performance": [
            {"id": i, "name": n, "status": s, "leads": c} for i, n, s, c in campaigns
        ],
    }
