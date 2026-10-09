from app.database import get_session
from app.logging_config import get_logger
from app.services.website_audit import run_audit
from workers.celery_app import celery_app

logger = get_logger("celery.audits")


@celery_app.task(name="workers.tasks.audits.run_website_audit")
def run_website_audit(audit_id: str) -> dict:
    logger.info("task_start", task="run_website_audit", audit_id=audit_id)
    db = get_session()
    try:
        status = run_audit(db, audit_id)
        return {"audit_id": audit_id, "status": status}
    finally:
        db.close()
        # A Claude slot is free again: start the next waiting lead now instead of at the next poll.
        from app.services.auto_audits import request_poll

        request_poll()


@celery_app.task(name="workers.tasks.audits.redraft_audit_email")
def redraft_audit_email(audit_id: str) -> dict:
    from app.services.website_audit import redraft_email

    logger.info("task_start", task="redraft_audit_email", audit_id=audit_id)
    db = get_session()
    try:
        return {"audit_id": audit_id, "status": redraft_email(db, audit_id)}
    finally:
        db.close()


@celery_app.task(name="workers.tasks.audits.poll_auto_audits")
def poll_auto_audits_task() -> dict:
    from app.services.auto_audits import poll_auto_audits

    db = get_session()
    try:
        return poll_auto_audits(db)
    finally:
        db.close()


@celery_app.task(name="workers.tasks.audits.check_inboxes")
def check_inboxes_task() -> dict:
    from app.services.inbox import check_all_inboxes

    db = get_session()
    try:
        return check_all_inboxes(db)
    finally:
        db.close()
