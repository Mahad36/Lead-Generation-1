from datetime import UTC, datetime, timedelta

from app.database import get_session
from app.logging_config import get_logger
from app.models import RefreshToken, ScrapingJob
from workers.celery_app import celery_app

logger = get_logger("celery.maintenance")


@celery_app.task(name="workers.tasks.maintenance.cleanup_jobs")
def cleanup_jobs() -> dict:
    db = get_session()
    try:
        cutoff = datetime.now(UTC) - timedelta(days=30)
        tokens = (
            db.query(RefreshToken)
            .filter(RefreshToken.expires_at < datetime.now(UTC))
            .delete(synchronize_session=False)
        )
        stale = (
            db.query(ScrapingJob)
            .filter(ScrapingJob.status == "running", ScrapingJob.updated_at < cutoff)
            .update({"status": "failed", "error_message": "Stale job cleaned up"}, synchronize_session=False)
        )
        db.commit()
        logger.info("cleanup_complete", tokens=tokens, stale_jobs=stale)
        return {"tokens": tokens, "stale_jobs": stale}
    finally:
        db.close()
