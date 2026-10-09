"""In-process scheduler for running without Redis/Celery (e.g. directly on Windows).

Runs the automatic audit poller and the reply/bounce inbox checker in background threads of the API process. Audits it starts run in
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


def start_inline_scheduler() -> None:
    global _started
    if _started or not get_settings().inline_scheduler:
        return
    _started = True
    threading.Thread(target=_loop, name="auto-audit-poller", daemon=True).start()
    if get_settings().inbox_check_seconds > 0:
        threading.Thread(target=_inbox_loop, name="inbox-checker", daemon=True).start()


def wake_poller() -> None:
    """Run the automatic-audit poller now rather than at the next tick."""
    _wake.set()


def stop_inline_scheduler() -> None:
    _stop.set()
    _wake.set()
