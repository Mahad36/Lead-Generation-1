"""Automatic outreach poller, per scrape file.

Each scrape file (ScrapingJob) has its own outreach switch: off / running / paused / stopped, a send mode (send
automatically or keep for review) and its own daily limit. Every few minutes (Celery beat, or the in-process
scheduler) and right after scraped leads are ready (`request_poll`), the poller looks at the files whose outreach is
running and starts the normal audit workflow (screenshots -> Claude CRO review -> PDF -> email -> send) for leads of
that file only, within that file's daily limit. Old files never run unless their outreach is started.

Shared across files: the Claude capacity (AUTO_AUDIT_BATCH_SIZE audits at once) and the email account's own daily
send limit (a safety cap for the mailbox). Failed audits are retried after a cooldown (AUTO_AUDIT_MAX_ATTEMPTS),
failed sends a few times (AUTO_SEND_MAX_ATTEMPTS). Each lead is its own job, so one bad website never stops a file.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_config import get_logger
from app.models import Campaign, CampaignLead, EmailAccount, Lead, ScrapingJob, SuppressionEntry, WebsiteAudit
from app.services.campaigns import (
    CAMPAIGN_PAUSED_STEP,
    CAMPAIGN_STOPPED_STEP,
    OPEN_CAMPAIGN_LEAD_STATUSES,
    OPEN_CAMPAIGN_STATUSES,
    campaign_hold,
)
from app.services.integrations import ai_is_configured
from app.services.website_audit import (
    ACTIVE_STATUSES,
    WAITING_FOR_LIMIT,
    WAITING_STEPS,
    AuditSendError,
    send_audit,
    start_audit,
)
from app.services.deliverability import WAITING_FOR_SCHEDULE, WAITING_FOR_WINDOW, address_problem, waiting_step
from app.utils.dates import local_day_start

logger = get_logger("auto_audits")

OUTREACH_STATUSES = ("off", "running", "paused", "stopped")


def _today_start() -> datetime:
    return local_day_start()  # the user's midnight, not UTC


def candidate_leads_query(db: Session, user_id: str, job_id: str | None = None):
    """Leads ready for an automatic audit: website + email, not audited yet (or a failed audit that may be retried),
    never contacted, not suppressed, not waiting in a campaign. `job_id` limits it to one scrape file."""
    app = get_settings()
    # Any audit that didn't fail blocks the lead; failed ones block it during the cooldown and after the last attempt.
    audited = select(WebsiteAudit.lead_id).where(
        WebsiteAudit.user_id == user_id, WebsiteAudit.status.not_in(["failed", "retry"])
    )
    cooling = select(WebsiteAudit.lead_id).where(
        WebsiteAudit.user_id == user_id,
        WebsiteAudit.status == "failed",
        WebsiteAudit.updated_at >= datetime.now(UTC) - timedelta(minutes=app.auto_audit_retry_minutes),
    )
    exhausted = (
        select(WebsiteAudit.lead_id)
        .where(WebsiteAudit.user_id == user_id, WebsiteAudit.status == "failed")
        .group_by(WebsiteAudit.lead_id)
        .having(func.count(WebsiteAudit.id) >= max(1, app.auto_audit_max_attempts))
    )
    # Leads queued in an open campaign are handled by that campaign (template or audit), never twice.
    in_campaign = (
        select(CampaignLead.lead_id)
        .join(Campaign, Campaign.id == CampaignLead.campaign_id)
        .where(
            Campaign.user_id == user_id,
            Campaign.status.in_(OPEN_CAMPAIGN_STATUSES),
            CampaignLead.status.in_(OPEN_CAMPAIGN_LEAD_STATUSES),
        )
    )
    suppressed = select(SuppressionEntry.email).where(SuppressionEntry.user_id == user_id)
    query = db.query(Lead).filter(
        Lead.user_id == user_id,
        Lead.website_url != "",
        Lead.primary_email != "",
        Lead.do_not_contact.is_(False),
        Lead.last_contacted_at.is_(None),
        Lead.status != "processing",
        Lead.source != "seed",  # demo sample businesses with fake websites
        Lead.email_status != "undeliverable",  # its email domain can't receive mail
        Lead.id.not_in(audited),
        Lead.id.not_in(cooling),
        Lead.id.not_in(exhausted),
        Lead.id.not_in(in_campaign),
        func.lower(Lead.primary_email).not_in(suppressed),
    )
    if job_id:
        query = query.filter(Lead.scraping_job_id == job_id)
    return query.order_by(Lead.created_at.asc())


def outreach_issues(db: Session, user_id: str, auto_send: bool = True) -> list[str]:
    """What stops automatic outreach from working at all (shown next to the outreach controls)."""
    issues = []
    if not ai_is_configured(db, user_id):
        issues.append("AI is not set up: choose Claude Code in AI & settings.")
    if auto_send:
        connected = (
            db.query(EmailAccount)
            .filter(EmailAccount.user_id == user_id, EmailAccount.status == "connected")
            .first()
        )
        if not connected:
            issues.append("No connected email account: connect Gmail or SMTP in Email setup.")
    return issues


def _job_audits(db: Session, job: ScrapingJob):
    return (
        db.query(WebsiteAudit)
        .join(Lead, Lead.id == WebsiteAudit.lead_id)
        .filter(WebsiteAudit.user_id == job.user_id, WebsiteAudit.trigger == "auto", Lead.scraping_job_id == job.id)
    )


def job_outreach_status(db: Session, job: ScrapingJob) -> dict:
    today = _today_start()
    audits = _job_audits(db, job)
    account = db.get(EmailAccount, job.outreach_email_account_id) if job.outreach_email_account_id else None
    return {
        "status": job.outreach_status,
        # Sends from this account; none chosen = your default account
        "email_account_id": account.id if account else None,
        "email_account": account.email_address if account else "",
        "email_account_issue": (
            f"{account.email_address} is not connected, so your default account sends instead. "
            "Reconnect it in Email setup or choose another."
            if account and account.status != "connected"
            else ""
        ),
        "auto_send": job.outreach_auto_send,
        "daily_limit": job.outreach_daily_limit,  # 0 = no daily audit limit
        # Progress over the whole file
        "audited_total": audits.filter(WebsiteAudit.status.not_in(["failed", "retry", "cancelled", *ACTIVE_STATUSES])).count(),
        "failed_total": audits.filter(WebsiteAudit.status == "failed").count(),
        "send_failed_total": audits.filter(WebsiteAudit.status == "send_failed").count(),
        "waiting_to_send": audits.filter(
            WebsiteAudit.status == "ready", WebsiteAudit.sent_at.is_(None), WebsiteAudit.auto_send.is_(True)
        ).count(),
        # Failed audits don't use up the daily allowance (retries are capped by AUTO_AUDIT_MAX_ATTEMPTS).
        "audits_today": audits.filter(WebsiteAudit.created_at >= today, WebsiteAudit.status != "failed").count(),
        "sent_today": audits.filter(WebsiteAudit.sent_at >= today).count(),
        "sent_total": audits.filter(WebsiteAudit.sent_at.isnot(None)).count(),
        "running": audits.filter(WebsiteAudit.status.in_(ACTIVE_STATUSES)).count(),
        "failed_today": audits.filter(
            WebsiteAudit.created_at >= today, WebsiteAudit.status.in_(["failed", "send_failed"])
        ).count(),
        "waiting_leads": candidate_leads_query(db, job.user_id, job.id).count(),
        "poll_seconds": get_settings().auto_audit_poll_seconds,
    }


def status_for_user(db: Session, user_id: str) -> dict:
    """Account-wide outreach health (the per-file numbers come with each scrape file)."""
    return {"issues": outreach_issues(db, user_id), "poll_seconds": get_settings().auto_audit_poll_seconds}


# ---------------------------------------------------------------- holds (pause / stop)


def outreach_hold(db: Session, audit: WebsiteAudit) -> str:
    """"paused" / "stopped" when the audit's campaign or scrape file is paused or stopped, else ""."""
    if audit.trigger == "campaign":
        return campaign_hold(db, audit)
    if audit.trigger != "auto":
        return ""
    status = (
        db.query(ScrapingJob.outreach_status)
        .join(Lead, Lead.scraping_job_id == ScrapingJob.id)
        .filter(Lead.id == audit.lead_id)
        .scalar()
    )
    return status if status in {"paused", "stopped"} else ""


def own_email_account_id(db: Session, user_id: str, account_id: str | None) -> str | None:
    """The id of one of this user's email accounts, or None for "my default account"."""
    if not account_id:
        return None
    account = (
        db.query(EmailAccount).filter(EmailAccount.id == account_id, EmailAccount.user_id == user_id).first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="Email account not found")
    return account.id


def _sending_account_id(db: Session, job: ScrapingJob) -> str | None:
    """The file's chosen account while it is connected; otherwise None (the default account sends)."""
    if not job.outreach_email_account_id:
        return None
    account = db.get(EmailAccount, job.outreach_email_account_id)
    if account and account.status == "connected":
        return account.id
    logger.warning("file_outreach_account_unavailable", job_id=job.id, account_id=job.outreach_email_account_id)
    return None


def set_job_outreach(
    db: Session,
    job: ScrapingJob,
    *,
    action: str,
    auto_send: bool | None = None,
    daily_limit: int | None = None,
    email_account_id=...,  # an account id, "" / None for your default account, ... to leave it unchanged
) -> ScrapingJob:
    """start / pause / resume / stop / update one file's outreach."""
    if email_account_id is not ...:
        job.outreach_email_account_id = own_email_account_id(db, job.user_id, email_account_id)
    if auto_send is not None:
        job.outreach_auto_send = auto_send
    if daily_limit is not None:
        job.outreach_daily_limit = max(0, int(daily_limit))  # 0 = no daily audit limit
    if action in {"start", "resume"}:
        job.outreach_status = "running"
    elif action == "pause" and job.outreach_status == "running":
        job.outreach_status = "paused"
    elif action == "stop":
        job.outreach_status = "stopped"
        # Audits already running finish without sending; their drafts stay on the lead page.
        for audit in _job_audits(db, job).filter(
            WebsiteAudit.sent_at.is_(None),
            WebsiteAudit.status.in_([*ACTIVE_STATUSES, "ready"]),
        ):
            audit.auto_send = False
            if audit.status == "ready":
                audit.step = CAMPAIGN_STOPPED_STEP
    elif action == "retry_failed":
        # Failed audits get another go (the poller picks their leads up again)
        for audit in _job_audits(db, job).filter(WebsiteAudit.status == "failed"):
            audit.status, audit.step = "retry", "Cleared for another attempt"
    elif action != "update":
        raise HTTPException(status_code=400, detail="Unknown outreach action")
    db.commit()
    db.refresh(job)
    logger.info("file_outreach_changed", job_id=job.id, action=action, status=job.outreach_status)
    if job.outreach_status == "running":
        release_held_sends(db, job)
        request_poll()
    return job


def release_held_sends(db: Session, job: ScrapingJob) -> int:
    """After a resume: send the emails whose audits finished while the file's outreach was paused."""
    held = _job_audits(db, job).filter(
        WebsiteAudit.status == "ready", WebsiteAudit.sent_at.is_(None), WebsiteAudit.step == CAMPAIGN_PAUSED_STEP
    ).all()
    sent = 0
    for audit in held:
        if not audit.auto_send:
            audit.step = "Ready to review and send"
            db.commit()
            continue
        try:
            send_audit(db, audit.user_id, audit.id)
            sent += 1
        except HTTPException as exc:
            db.rollback()
            if exc.status_code == 429:
                audit = db.get(WebsiteAudit, audit.id)
                audit.step = waiting_step(exc, WAITING_FOR_LIMIT)  # send_waiting sends it when allowed
                db.commit()
                break
        except AuditSendError:
            pass  # recorded on the audit (send_failed)
    return sent


# ---------------------------------------------------------------- polling


RETRY_FIRST_MINUTES = 10


def retry_wait_minutes(attempts: int) -> int:
    """Wait before re-sending after `attempts` failed tries: 10, 20, 40, ... minutes (at most 6 hours)."""
    return min(RETRY_FIRST_MINUTES * 2 ** max((attempts or 1) - 1, 0), 360)


def retry_failed_sends(db: Session) -> int:
    """Re-send automatic emails that failed to send (e.g. Gmail's "421 server busy"), a limited number of times.

    Covers scrape files, campaigns and manual audits set to send automatically; paused / stopped ones wait.
    """
    app = get_settings()
    now = datetime.now(UTC)
    rows = (
        db.query(WebsiteAudit)
        .filter(
            WebsiteAudit.auto_send.is_(True),
            WebsiteAudit.status == "send_failed",
            WebsiteAudit.send_retryable.is_(True),  # permanent failures (bad address, blocked) are never retried
            WebsiteAudit.send_attempts < app.auto_send_max_attempts,
            WebsiteAudit.updated_at <= now - timedelta(minutes=RETRY_FIRST_MINUTES),
        )
        .order_by(WebsiteAudit.updated_at.asc())
        .limit(20)
        .all()
    )
    sent = 0
    for audit in rows:
        if audit.updated_at > now - timedelta(minutes=retry_wait_minutes(audit.send_attempts)):
            continue  # exponential backoff: 10, 20, 40... minutes after each failed attempt
        if outreach_hold(db, audit):
            continue
        try:
            send_audit(db, audit.user_id, audit.id)
            sent += 1
        except AuditSendError:
            pass  # recorded on the audit; retried next time until attempts run out
        except HTTPException as exc:  # not retryable (suppressed, daily limit, missing PDF...)
            db.rollback()
            if exc.status_code == 429:
                break
            audit = db.get(WebsiteAudit, audit.id)
            audit.send_attempts, audit.error_message = app.auto_send_max_attempts, str(exc.detail)
            db.commit()
    return sent


def _running_audits(db: Session, user_id: str) -> int:
    # All running audits (files, campaigns, manual) share the Claude capacity.
    return db.query(WebsiteAudit).filter(WebsiteAudit.user_id == user_id, WebsiteAudit.status.in_(ACTIVE_STATUSES)).count()


def poll_job(db: Session, job: ScrapingJob) -> int:
    """Start automatic audits for one file, within its own daily limit. Returns how many were started."""
    log = logger.bind(user_id=job.user_id, job_id=job.id)
    issues = outreach_issues(db, job.user_id, job.outreach_auto_send)
    if issues:
        log.info("file_outreach_skipped", reasons=issues)
        return 0
    today = _today_start()
    used_today = _job_audits(db, job).filter(WebsiteAudit.created_at >= today, WebsiteAudit.status != "failed").count()
    batch = max(1, get_settings().auto_audit_batch_size)
    # Audits are not tied to sending capacity: finished emails simply wait for an inbox with room.
    # The real limits are the parallel audits (Claude + a browser each) and the optional daily audit limit (0 = none).
    room = batch - _running_audits(db, job.user_id)
    if job.outreach_daily_limit:
        room = min(room, job.outreach_daily_limit - used_today)
    if room <= 0:
        return 0
    started = 0
    for lead in candidate_leads_query(db, job.user_id, job.id).limit(room).all():
        problem = address_problem(db, job.user_id, lead.primary_email)
        if problem:
            # Don't spend an audit on an email that would bounce
            lead.email_status = "undeliverable"
            lead.notes = (lead.notes + "\n" if lead.notes else "") + problem
            db.commit()
            log.info("auto_audit_skipped_undeliverable", lead_id=lead.id)
            continue
        try:
            start_audit(
                db,
                job.user_id,
                lead.id,
                auto_send=job.outreach_auto_send,
                email_account_id=_sending_account_id(db, job),
                trigger="auto",
            )
            started += 1
        except HTTPException as exc:
            db.rollback()
            log.warning("auto_audit_start_failed", lead_id=lead.id, error=str(exc.detail))
    if started:
        log.info("auto_audits_started", count=started, used_today=used_today + started, limit=job.outreach_daily_limit)
    return started


def request_poll() -> None:
    """Run the poller now instead of waiting for the next tick (e.g. scraped leads just became ready)."""
    settings = get_settings()
    if settings.inline_scheduler:
        from app.services.scheduler import wake_poller

        wake_poller()
        return
    try:
        from app.services.scraping_jobs import broker_available

        if broker_available():
            from workers.tasks.audits import poll_auto_audits_task

            poll_auto_audits_task.delay()
    except Exception as exc:  # the regular tick still picks the leads up
        logger.warning("auto_audit_poll_request_failed", error=str(exc))


def send_waiting(db: Session) -> int:
    """Send emails that were held back by the daily send limit, as soon as the limit allows (any trigger)."""
    waiting = (
        db.query(WebsiteAudit)
        .filter(
            WebsiteAudit.status == "ready",
            WebsiteAudit.auto_send.is_(True),
            WebsiteAudit.sent_at.is_(None),
            WebsiteAudit.step.in_(WAITING_STEPS),
        )
        .order_by(WebsiteAudit.created_at.asc())
        .limit(50)
        .all()
    )
    sent, full, used = 0, set(), set()
    for audit in waiting:
        if audit.email_account_id and audit.email_account_id in used:
            continue  # one held email per inbox per round, so a backlog doesn't go out in one burst
        if audit.email_account_id in full or outreach_hold(db, audit):
            continue  # inbox full today, or its file / campaign is paused or stopped
        try:
            sent_audit = send_audit(db, audit.user_id, audit.id)
            sent += 1
            used.add(sent_audit.email_account_id)
        except HTTPException as exc:
            db.rollback()
            if exc.status_code == 429:
                if getattr(exc, "step", "") not in (WAITING_FOR_WINDOW, WAITING_FOR_SCHEDULE):
                    full.add(audit.email_account_id)  # still at today's limit (or paused): try again later
                # (sending hours / schedules are per lead: the next lead may be in another time zone)
                held = db.get(WebsiteAudit, audit.id)
                if held and held.step != waiting_step(exc, held.step):
                    held.step = waiting_step(exc, held.step)  # show the current reason
                    db.commit()
            else:
                logger.warning("waiting_send_failed", audit_id=audit.id, error=str(exc.detail))
        except AuditSendError:
            pass  # recorded on the audit (send_failed)
    if sent:
        logger.info("waiting_emails_sent", count=sent)
    return sent


def poll_auto_audits(db: Session) -> dict:
    try:
        send_waiting(db)
        retry_failed_sends(db)
    except Exception as exc:  # never block the poller
        db.rollback()
        logger.error("send_waiting_failed", error=str(exc))
    jobs = (
        db.query(ScrapingJob)
        .filter(ScrapingJob.outreach_status == "running")
        .order_by(ScrapingJob.created_at.asc())
        .all()
    )
    started = 0
    for job in jobs:
        try:
            started += poll_job(db, job)
        except Exception as exc:  # one file's problem must not stop the others
            db.rollback()
            logger.error("file_outreach_poll_failed", job_id=job.id, error=str(exc))
    return {"files": len(jobs), "started": started}
