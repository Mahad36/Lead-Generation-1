from app.config import get_settings
from app.logging_config import get_logger
from app.models import Lead, LeadImport

logger = get_logger("lead_pipeline")


def _broker_available() -> bool:
    try:
        import redis

        client = redis.Redis.from_url(get_settings().celery_broker_url, socket_connect_timeout=1)
        client.ping()
        return True
    except Exception:
        return False


def refresh_import_batch(db, batch_id: str | None) -> LeadImport | None:
    if not batch_id:
        return None
    batch = db.get(LeadImport, batch_id)
    if not batch:
        return None
    from app.services.leads import import_batch_filter

    leads = db.query(Lead).filter(import_batch_filter(batch)).all()
    batch.lead_count = len(leads)
    batch.ready_leads = sum(1 for lead in leads if lead.status == "ready" and (lead.primary_email or "").strip())
    batch.failed_leads = sum(1 for lead in leads if lead.status == "failed")
    batch.analyzed_leads = sum(1 for lead in leads if lead.status in {"ready", "failed", "no_email"})
    if batch.lead_count == 0:
        batch.status = "ready"
        batch.error_message = batch.error_message or "No leads with an email address were kept"
    elif batch.analyzed_leads >= batch.lead_count:
        batch.status = "ready"
    else:
        batch.status = "processing"
    db.add(batch)
    db.commit()
    return batch


def enqueue_lead_website_processing(lead_ids: list[str]) -> None:
    if not lead_ids:
        return

    def _run() -> None:
        from app.database import get_session
        from app.services.website_pipeline import process_lead_website

        db = get_session()
        try:
            for lead_id in lead_ids:
                try:
                    process_lead_website(db, lead_id)
                except Exception as exc:
                    logger.warning("deferred_website_failed", lead_id=lead_id, error=str(exc))
                    try:
                        from app.models import Lead
                        from app.services.website_pipeline import _mark_lead_status

                        lead = db.get(Lead, lead_id)
                        if lead:
                            _mark_lead_status(db, lead, "failed")
                    except Exception:
                        logger.warning("lead_status_update_failed", lead_id=lead_id)
        finally:
            db.close()

    if _broker_available():
        from workers.tasks.pipeline import process_scraped_lead

        for lead_id in lead_ids:
            process_scraped_lead.delay(lead_id)
        return
    import threading

    threading.Thread(target=_run, daemon=True).start()
