import threading
import time
from datetime import UTC, datetime

from app.database import get_session
from app.logging_config import get_logger
from app.models import Campaign, CampaignLead
from app.services.campaigns import (
    campaign_has_work,
    claim_next_campaign_lead,
    generate_for_campaign_lead,
    recover_stale_campaign_leads,
    reset_counters_if_needed,
    send_claimed_lead,
)
from workers.celery_app import celery_app

logger = get_logger("celery.campaigns")


def _continue_queue(campaign_id: str, delay: int = 0) -> None:
    from app.services.scraping_jobs import broker_available

    if broker_available():
        if delay:
            process_campaign_queue.apply_async(args=[campaign_id], countdown=delay)
        else:
            process_campaign_queue.delay(campaign_id)
        return

    def run() -> None:
        if delay:
            time.sleep(max(delay, 0))
        process_campaign_queue(campaign_id)

    threading.Thread(target=run, daemon=True).start()


@celery_app.task(name="workers.tasks.campaigns.generate_personalized_email")
def generate_personalized_email_task(campaign_lead_id: str) -> dict:
    db = get_session()
    try:
        item = generate_for_campaign_lead(db, campaign_lead_id)
        return {"id": item.id, "status": item.status}
    finally:
        db.close()


@celery_app.task(name="workers.tasks.campaigns.launch_campaign")
def launch_campaign(campaign_id: str) -> dict:
    from app.services.campaigns import launch_campaign as launch

    db = get_session()
    try:
        campaign = db.get(Campaign, campaign_id)
        if not campaign:
            return {"status": "missing"}
        launch(db, campaign.user_id, campaign_id, {})
        from app.services.scraping_jobs import enqueue_task

        enqueue_task(process_campaign_queue, campaign_id)
        return {"status": "launched"}
    finally:
        db.close()


@celery_app.task(name="workers.tasks.campaigns.generate_campaign_emails")
def generate_campaign_emails(campaign_id: str) -> dict:
    db = get_session()
    try:
        items = (
            db.query(CampaignLead)
            .filter(CampaignLead.campaign_id == campaign_id, CampaignLead.status == "pending")
            .all()
        )
        for item in items:
            generate_for_campaign_lead(db, item.id)
        return {"generated": len(items)}
    finally:
        db.close()


@celery_app.task(name="workers.tasks.campaigns.process_campaign_queue")
def process_campaign_queue(campaign_id: str) -> dict:
    db = get_session()
    worker_id = f"worker-{datetime.now(UTC).timestamp()}"
    sent = 0
    try:
        campaign = db.get(Campaign, campaign_id)
        if not campaign:
            return {"status": "skipped"}
        reset_counters_if_needed(campaign)
        recover_stale_campaign_leads(db, campaign_id)
        if campaign.status == "scheduled":
            raw = (campaign.extra or {}).get("scheduled_at")
            start_at = datetime.fromisoformat(raw) if raw else None
            if start_at and start_at.tzinfo is None:
                start_at = start_at.replace(tzinfo=UTC)
            now = datetime.now(UTC)
            if start_at and start_at > now:
                _continue_queue(campaign_id, max(int((start_at - now).total_seconds()) + 1, 1))
                return {"status": "scheduled"}
            from app.services.campaigns import launch_campaign as launch

            launch(db, campaign.user_id, campaign_id, {})
            db.refresh(campaign)
        if campaign.status == "paused" and campaign.paused_reason == "daily_limit_reached" and campaign.sent_today == 0:
            campaign.status = "running"
            campaign.paused_reason = ""
        if campaign.status == "completed" and campaign_has_work(db, campaign_id) and campaign.sent_today < campaign.daily_limit:
            campaign.status = "running"
            campaign.paused_reason = ""
        db.commit()
        if campaign.status != "running":
            return {"status": "skipped"}
        now = datetime.now(UTC)
        if campaign.next_eligible_at and campaign.next_eligible_at > now:
            delay = max(int((campaign.next_eligible_at - now).total_seconds()) + 1, 1)
            _continue_queue(campaign_id, delay)
            return {"status": "waiting", "next": campaign.next_eligible_at.isoformat()}
        if campaign.end_date and datetime.now(UTC).date() > campaign.end_date:
            campaign.status = "completed"
            db.commit()
            return {"status": "completed"}
        from app.services.campaigns import advance_audit_campaign, is_audit_campaign

        if is_audit_campaign(campaign):
            result, delay = advance_audit_campaign(db, campaign)
            if delay is not None:
                _continue_queue(campaign_id, delay)
            return {"status": result}
        claimed = claim_next_campaign_lead(db, campaign_id, worker_id)
        if not claimed:
            pending_gen = (
                db.query(CampaignLead)
                .filter(CampaignLead.campaign_id == campaign_id, CampaignLead.status.in_(["pending", "generating"]))
                .first()
            )
            if pending_gen:
                generate_for_campaign_lead(db, pending_gen.id)
                _continue_queue(campaign_id)
                return {"status": "generated"}
            if campaign_has_work(db, campaign_id):
                _continue_queue(campaign_id, 10)
                return {"status": "retrying"}
            extra = campaign.extra or {}
            if extra.get("auto") and extra.get("file_kind") == "scrape":
                from app.models import ScrapingJob, Lead

                job = db.get(ScrapingJob, extra.get("file_id"))
                still_working = bool(job and job.status in {"queued", "running", "paused", "draft"})
                still_processing = bool(
                    extra.get("file_id")
                    and db.query(Lead)
                    .filter(Lead.scraping_job_id == extra.get("file_id"), Lead.status == "processing")
                    .first()
                )
                if still_working or still_processing:
                    _continue_queue(campaign_id, 15)
                    return {"status": "waiting_leads"}
                campaign.status = "completed"
                db.commit()
                return {"status": "completed"}
            return {"status": "idle"}
        result = send_claimed_lead(db, claimed)
        sent = 1 if result == "sent" else 0
        if result == "sent":
            db.refresh(campaign)
            delay = 0
            if campaign.next_eligible_at:
                delay = max(int((campaign.next_eligible_at - datetime.now(UTC)).total_seconds()), 0)
            _continue_queue(campaign_id, delay)
        elif result == "hourly_limit":
            _continue_queue(campaign_id, 3600)
        elif result == "daily_limit":
            from app.utils.dates import seconds_until_local_midnight

            _continue_queue(campaign_id, seconds_until_local_midnight())
        return {"status": result, "sent": sent}
    except Exception as exc:
        logger.error("campaign_queue_failed", campaign_id=campaign_id, error=str(exc))
        raise
    finally:
        db.close()


@celery_app.task(name="workers.tasks.campaigns.send_campaign_email")
def send_campaign_email(campaign_lead_id: str) -> dict:
    db = get_session()
    try:
        item = db.get(CampaignLead, campaign_lead_id)
        if not item:
            return {"status": "missing"}
        item.status = "sending"
        item.locked_at = datetime.now(UTC)
        item.locked_by = "direct"
        db.commit()
        return {"status": send_claimed_lead(db, item)}
    finally:
        db.close()


@celery_app.task(name="workers.tasks.campaigns.retry_failed_email")
def retry_failed_email(campaign_lead_id: str) -> dict:
    db = get_session()
    try:
        item = db.get(CampaignLead, campaign_lead_id)
        if not item or item.status != "failed":
            return {"status": "skipped"}
        item.status = "queued"
        item.locked_at = None
        item.locked_by = ""
        db.commit()
        process_campaign_queue.delay(item.campaign_id)
        return {"status": "requeued"}
    finally:
        db.close()


@celery_app.task(name="workers.tasks.campaigns.process_all_running_campaigns")
def process_all_running_campaigns() -> dict:
    db = get_session()
    try:
        running = db.query(Campaign).filter(Campaign.status.in_(["running", "scheduled"])).all()
        paused_daily = db.query(Campaign).filter(
            Campaign.status == "paused", Campaign.paused_reason == "daily_limit_reached"
        ).all()
        today = datetime.now(UTC).date()
        for campaign in paused_daily:
            if campaign.sent_today_date != today:
                campaign.status = "running"
                campaign.paused_reason = ""
                campaign.sent_today = 0
                campaign.sent_today_date = today
                db.commit()
                process_campaign_queue.delay(campaign.id)
        for campaign in running:
            process_campaign_queue.delay(campaign.id)
        return {"running": len(running), "resumed": len(paused_daily)}
    finally:
        db.close()


@celery_app.task(name="workers.tasks.campaigns.daily_campaign_reset")
def daily_campaign_reset() -> dict:
    db = get_session()
    try:
        today = datetime.now(UTC).date()
        campaigns = db.query(Campaign).all()
        reset = 0
        for campaign in campaigns:
            if campaign.sent_today_date != today:
                campaign.sent_today = 0
                campaign.sent_today_date = today
                reset += 1
            if campaign.status == "paused" and campaign.paused_reason == "daily_limit_reached":
                if not campaign.end_date or campaign.end_date >= today:
                    campaign.status = "running"
                    campaign.paused_reason = ""
                    process_campaign_queue.delay(campaign.id)
        db.commit()
        return {"reset": reset}
    finally:
        db.close()
