"""In-process scheduler for running without Redis/Celery (e.g. directly on Windows).

Runs the automatic audit poller, the reply/bounce inbox checker and the deliverability round (follow-ups, warm-up)
in background threads of the API process. Audits it starts run in
threads too (see scraping_jobs.enqueue_task), so no broker or worker process is needed.
"""

from __future__ import annotations

import threading

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger("scheduler")
_started = False
_stop = threading.Event()
_wake = threading.Event()


def _loop() -> None:
    from app.database import get_session
    from app.services.auto_audits import poll_auto_audits

    interval = max(30, get_settings().auto_audit_poll_seconds)
    logger.info("inline_scheduler_started", poll_seconds=interval)
    while not _stop.is_set():
        _wake.wait(interval)
        _wake.clear()
        if _stop.is_set():
            break
        db = get_session()
        try:
            result = poll_auto_audits(db)
            if result.get("started"):
                logger.info("inline_poll", **result)
        except Exception as exc:  # keep polling after a bad cycle
            logger.error("inline_poll_failed", error=str(exc))
        finally:
            db.close()


def _inbox_loop() -> None:
    from app.database import get_session
    from app.services.inbox import check_all_inboxes

    interval = max(60, get_settings().inbox_check_seconds)
    logger.info("inbox_checker_started", check_seconds=interval)
    while not _stop.wait(interval):
        db = get_session()
        try:
            check_all_inboxes(db)
        except Exception as exc:  # keep checking after a bad cycle
            logger.error("inbox_check_cycle_failed", error=str(exc))
        finally:
            db.close()


DELIVERABILITY_SECONDS = 300


def deliverability_round() -> dict:
    """Follow-ups that are due, then warm-up between your inboxes. Each part survives the other failing."""
    from app.database import get_session
    from app.services.account_health import assess_all
    from app.services.followups import run_followups
    from app.services.gmail_signature import refresh_due
    from app.services.warmup import run_warmup

    result = {}
    jobs = (
        ("account_health", assess_all),  # status + safe capacity of every account, before anything is sent
        ("signatures", refresh_due),
        ("followups", run_followups),
        ("warmup", run_warmup),
    )
    for name, job in jobs:
        db = get_session()
        try:
            result[name] = job(db)
        except Exception as exc:
            db.rollback()
            logger.error(f"{name}_round_failed", error=str(exc)[:300])
        finally:
            db.close()
    return result


def _deliverability_loop() -> None:
    logger.info("deliverability_loop_started", every_seconds=DELIVERABILITY_SECONDS)
    while not _stop.wait(DELIVERABILITY_SECONDS):
        deliverability_round()


def start_inline_scheduler() -> None:
    global _started
    if _started or not get_settings().inline_scheduler:
        return
    _started = True
    threading.Thread(target=_loop, name="auto-audit-poller", daemon=True).start()
    if get_settings().inbox_check_seconds > 0:
        threading.Thread(target=_inbox_loop, name="inbox-checker", daemon=True).start()
    threading.Thread(target=_deliverability_loop, name="followups-warmup", daemon=True).start()


def wake_poller() -> None:
    """Run the automatic-audit poller now rather than at the next tick."""
    _wake.set()


def stop_inline_scheduler() -> None:
    _stop.set()
    _wake.set()
