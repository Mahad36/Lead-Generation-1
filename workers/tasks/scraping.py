from app.database import get_session
from app.logging_config import get_logger
from app.services.scraping_jobs import run_scraping_job
from workers.celery_app import celery_app

logger = get_logger("celery.scraper")


@celery_app.task(name="workers.tasks.scraping.scrape_google_maps_job", bind=True)
def scrape_google_maps_job(self, job_id: str) -> dict:
    logger.info("task_start", task="scrape_google_maps", job_id=job_id)
    db = get_session()
    try:
        run_scraping_job(db, job_id)
        return {"job_id": job_id, "status": "done"}
    except Exception as exc:
        logger.error("task_failed", task="scrape_google_maps", job_id=job_id, error=str(exc))
        raise
    finally:
        db.close()
