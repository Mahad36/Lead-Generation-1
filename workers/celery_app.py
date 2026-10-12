import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
for path in (str(ROOT), str(API_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings
from app.logging_config import configure_logging

configure_logging("celery")
from app.utils.dns_fallback import install as install_dns_fallback  # noqa: E402

install_dns_fallback()
settings = get_settings()

celery_app = Celery(
    "leadgen",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "workers.tasks.scraping",
        "workers.tasks.pipeline",
        "workers.tasks.campaigns",
        "workers.tasks.maintenance",
        "workers.tasks.audits",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    beat_schedule={
        "process-running-campaigns": {
            "task": "workers.tasks.campaigns.process_all_running_campaigns",
            "schedule": 30.0,
        },
        "daily-campaign-reset": {
            "task": "workers.tasks.campaigns.daily_campaign_reset",
            "schedule": crontab(minute=5, hour=0),
        },
        "poll-auto-audits": {
            "task": "workers.tasks.audits.poll_auto_audits",
            "schedule": float(settings.auto_audit_poll_seconds),
        },
        "check-inboxes": {
            "task": "workers.tasks.audits.check_inboxes",
            "schedule": float(max(60, settings.inbox_check_seconds)),
        },
        "deliverability-round": {
            "task": "workers.tasks.audits.deliverability_round",
            "schedule": 300.0,
        },
        "cleanup-jobs": {
            "task": "workers.tasks.maintenance.cleanup_jobs",
            "schedule": crontab(minute=20, hour=3),
        },
    },
)
