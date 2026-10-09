from app.database import get_session
from app.logging_config import get_logger
from app.services.website_pipeline import process_lead_website
from workers.celery_app import celery_app

logger = get_logger("celery.pipeline")


@celery_app.task(name="workers.tasks.pipeline.process_scraped_lead", bind=True)
def process_scraped_lead(self, lead_id: str) -> dict:
    return analyze_website(lead_id)


@celery_app.task(name="workers.tasks.pipeline.crawl_website")
def crawl_website(lead_id: str) -> dict:
    return analyze_website(lead_id)


@celery_app.task(name="workers.tasks.pipeline.extract_emails")
def extract_emails(lead_id: str) -> dict:
    return analyze_website(lead_id)


@celery_app.task(name="workers.tasks.pipeline.detect_platform")
def detect_platform(lead_id: str) -> dict:
    return analyze_website(lead_id)


@celery_app.task(name="workers.tasks.pipeline.analyze_website")
def analyze_website(lead_id: str) -> dict:
    logger.info("task_start", task="analyze_website", lead_id=lead_id)
    db = get_session()
    try:
        website = process_lead_website(db, lead_id)
        return {"lead_id": lead_id, "website_id": website.id if website else None}
    except Exception as exc:
        logger.error("task_failed", task="analyze_website", lead_id=lead_id, error=str(exc))
        try:
            from app.models import Lead
            from app.services.website_pipeline import _mark_lead_status

            lead = db.get(Lead, lead_id)
            if lead:
                _mark_lead_status(db, lead, "failed")
        except Exception:
            logger.warning("lead_status_update_failed", lead_id=lead_id)
        raise
    finally:
        db.close()


@celery_app.task(name="workers.tasks.pipeline.calculate_website_score")
def calculate_website_score_task(lead_id: str) -> dict:
    return analyze_website(lead_id)


@celery_app.task(name="workers.tasks.pipeline.calculate_lead_score")
def calculate_lead_score_task(lead_id: str) -> dict:
    return analyze_website(lead_id)
