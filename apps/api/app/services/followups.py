"""Follow-ups: short emails in the same thread when a lead hasn't replied.

After each first email (audit email or campaign email) that has `followups_enabled`, step N goes out
`followup_days[N]` days after the previous email of the thread, from the same inbox, as a reply in the same Gmail
thread (In-Reply-To / References), without the PDF. Claude writes each one from the first email; a plain template
is used if the AI is unavailable. They stop for good as soon as the lead replies, bounces, unsubscribes or is marked
do-not-contact, when its campaign / scrape file is stopped, or after the last step (followups_done_at is set).
Paused campaigns / files, sending hours, daily limits and paused inboxes only delay them.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from html import escape

from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_config import get_logger
from app.models import Campaign, EmailAccount, EmailMessage, Lead, ScrapingJob, UserSettings, WebsiteAudit
from app.services.deliverability import account_ready, in_send_window, sent_recently
from app.services.gmail import GmailSendError, daily_limit_reached, send_gmail_message
from app.services.suppression import is_suppressed
from app.services.gmail_signature import account_signature
from app.utils.signature import append_signature

logger = get_logger("followups")

MAX_STEPS = 5
STOP_LEAD_STATUSES = {"replied", "bounced", "undeliverable"}

FOLLOWUP_SYSTEM = """You write short follow-up emails for a one-person web design studio doing cold outreach to small
businesses. The first email (given) offered a free piece of work based on a review of their website, with a PDF
audit attached. The owner hasn't replied.

{style}
Rules for the follow-up:
- 30-70 words, 2-3 short paragraphs, plain text (no HTML, no links unless one appears in the first email).
- Start with "Hi <recipient.first_name>," on its own line when a first name is given, otherwise exactly
  "Hi there,". Never greet the business name.
- Don't repeat the first email. Add one new, small, useful angle: a single specific detail from the audit, how quick
  the free offer is for them, or a simple question. The last follow-up can politely close the loop ("I'll leave it
  here").
- Never guilt-trip ("just bumping", "did you see my email", "following up on my previous email" are banned).
- No subject line, no P.S., no unsubscribe line, no placeholders.
- End with a short closing like "Best," with nothing after it (the signature is added automatically) when
  signature_added is true; otherwise end with the sender's first name.

Return JSON only: {{"body": "..."}}"""

FOLLOWUP_SCHEMA: dict = {
    "type": "object",
    "properties": {"body": {"type": "string"}},
    "required": ["body"],
    "additionalProperties": False,
}


def followup_days(settings: UserSettings | None) -> list[int]:
    """Days between the emails of a thread, e.g. "3,5" -> [3, 5] (follow-up 1 after 3 days, then 5 more)."""
    raw = (settings.followup_days if settings else "") or "3,5"
    days = []
    for part in re.split(r"[,\s;]+", raw):
        if part.strip().isdigit():
            days.append(max(1, min(int(part), 60)))
    return days[:MAX_STEPS] or [3, 5]


def _settings(db: Session, user_id: str) -> UserSettings | None:
    return db.query(UserSettings).filter(UserSettings.user_id == user_id).first()


def _thread(db: Session, root: EmailMessage) -> list[EmailMessage]:
    followups = (
        db.query(EmailMessage)
        .filter(EmailMessage.followup_of_id == root.id, EmailMessage.status == "sent")
        .order_by(EmailMessage.followup_step.asc())
        .all()
    )
    return [root, *followups]


def _origin(db: Session, root: EmailMessage) -> tuple[Campaign | None, ScrapingJob | None, WebsiteAudit | None]:
    audit = db.query(WebsiteAudit).filter(WebsiteAudit.email_message_id == root.id).first()
    campaign = db.get(Campaign, root.campaign_id) if root.campaign_id else None
    job = None
    if audit and audit.trigger == "campaign":
        from app.services.campaigns import campaign_for_audit

        campaign = campaign_for_audit(db, audit)
    elif audit and audit.trigger == "auto":
        lead = db.get(Lead, audit.lead_id)
        job = db.get(ScrapingJob, lead.scraping_job_id) if lead and lead.scraping_job_id else None
    return campaign, job, audit


def _stop_reason(db: Session, root: EmailMessage, settings: UserSettings | None) -> str:
    """Why this thread gets no more follow-ups ("" = it may continue)."""
    if settings is not None and not settings.followups_enabled:
        return "follow-ups switched off"
    if root.replied_at:
        return "replied"
    if root.bounced_at:
        return "bounced"
    lead = db.get(Lead, root.lead_id) if root.lead_id else None
    if lead and (lead.do_not_contact or lead.email_status in STOP_LEAD_STATUSES):
        return f"lead {lead.email_status if not lead.do_not_contact else 'do not contact'}"
    if is_suppressed(db, root.user_id, root.recipient_email):
        return "unsubscribed"
    # A reply on any of the thread's emails (matched by the inbox checker) also marks that email replied
    if db.query(EmailMessage.id).filter(EmailMessage.followup_of_id == root.id, EmailMessage.replied_at.isnot(None)).first():
        return "replied"
    account = db.get(EmailAccount, root.email_account_id) if root.email_account_id else None
    if not account or account.status == "disconnected":
        return "sending inbox removed"
    campaign, job, _ = _origin(db, root)
    if campaign and (campaign.status == "stopped" or (campaign.extra or {}).get("followups", True) is False):
        return "campaign stopped"
    if job and job.outreach_status == "stopped":
        return "file outreach stopped"
    return ""


def _on_hold(db: Session, root: EmailMessage) -> bool:
    """Paused campaign / file: wait (the thread continues when it resumes)."""
    campaign, job, _ = _origin(db, root)
    if campaign and campaign.status == "paused" and campaign.paused_reason != "daily_limit_reached":
        return True
    return bool(job and job.outreach_status == "paused")


def _send_time_ok(db: Session, root: EmailMessage) -> bool:
    """The file's / campaign's schedule in the lead's time zone, else your sending hours."""
    from app.services.deliverability import lead_timezone, send_time_ok

    campaign, job, _ = _origin(db, root)
    schedule = ((campaign.extra if campaign else None) or (job.extra if job else None) or {}).get("schedule")
    if schedule and schedule.get("mode") == "now":
        return send_time_ok(db, root.user_id, schedule, "")
    if not schedule or (schedule.get("mode") != "local" and not schedule.get("start_at")):
        return in_send_window(_settings(db, root.user_id))
    lead = db.get(Lead, root.lead_id) if root.lead_id else None
    return send_time_ok(db, root.user_id, schedule, lead_timezone(lead) if schedule.get("mode") == "local" else "")


def _adds_signature(db: Session, root: EmailMessage) -> bool:
    campaign, _, _ = _origin(db, root)
    return not campaign or (campaign.extra or {}).get("add_signature", True) is not False


def _first_name(lead: Lead | None, root: EmailMessage) -> str:
    """The owner's / recipient's first name, or "there" (never the business name)."""
    from app.utils.greeting import owner_first_name

    return owner_first_name(lead, getattr(root, "recipient_email", None)) or "there"


def _fallback_body(step: int, steps: int, lead: Lead | None, root: EmailMessage, signature_added: bool, sender: str) -> str:
    name = _first_name(lead, root)
    business = (lead.business_name if lead else "") or "your business"
    if step >= steps:
        text = (
            f"Hi {name},\n\nI'll leave it here so I don't fill your inbox. If improving the website for {business} "
            "comes up later, the audit I sent stays yours to use, and the offer stands.\n\n"
        )
    else:
        text = (
            f"Hi {name},\n\nIn case the audit got lost in a busy week: the free piece I offered for {business} takes "
            "me very little time, and you'd see it before deciding anything.\n\nWould it be useful?\n\n"
        )
    return text + ("Best," if signature_added else f"Best,\n{sender}")


def _write_body(
    db: Session, root: EmailMessage, step: int, steps: int, lead: Lead | None, settings: UserSettings | None,
    signature_added: bool,
) -> str:
    from app.services.ai import get_ai_provider_for_user
    from app.services.audit_ai import HUMAN_STYLE, parse_json_text

    sender = (settings.sender_name if settings else "") or ""
    thread = _thread(db, root)
    _, _, audit = _origin(db, root)
    report = (audit.report or {}) if audit else {}
    payload = {
        "follow_up_number": step,
        "total_follow_ups": steps,
        "is_last": step >= steps,
        "recipient": {
            "first_name": _first_name(lead, root) if _first_name(lead, root) != "there" else "",
            "business_name": lead.business_name if lead else root.recipient_name,
            "category": lead.category if lead else "",
            "city": lead.city if lead else "",
        },
        "first_email": root.text_body[:3000],
        "earlier_follow_ups": [m.text_body[:800] for m in thread[1:]],
        "audit_highlights": {
            "headline": report.get("headline", ""),
            "top_findings": [f.get("title", "") for f in (report.get("findings") or [])[:4]],
            "free_offer": report.get("free_offer") or {},
        },
        "sender": {"name": sender, "company": settings.company_name if settings else ""},
        "signature_added": signature_added,
    }
    try:
        provider = get_ai_provider_for_user(db, root.user_id)
        text = provider.generate(
            "Write follow-up email number "
            f"{step} of {steps}. Return JSON only.\n\n" + json.dumps(payload, ensure_ascii=False, default=str),
            system=FOLLOWUP_SYSTEM.format(style=HUMAN_STYLE),
            temperature=0.5,
            max_tokens=1200,
            json_schema=FOLLOWUP_SCHEMA,
            timeout=min(get_settings().audit_ai_timeout_seconds, 180),
        )
        body = str(parse_json_text(text).get("body") or "").strip()
        body = body.replace("—", ", ").replace("–", "-")
        if 15 <= len(body.split()) <= 160:
            return body
        logger.warning("followup_ai_bad_length", root_id=root.id, words=len(body.split()))
    except Exception as exc:  # AI not set up / busy / failed: the plain version still goes out
        logger.warning("followup_ai_failed", root_id=root.id, error=str(exc)[:200])
    return _fallback_body(step, steps, lead, root, signature_added, sender)


def _paragraphs_html(text: str) -> str:
    return "".join(
        f"<p>{escape(p.strip()).replace(chr(10), '<br>')}</p>" for p in re.split(r"\n\s*\n", text) if p.strip()
    )


def send_followup(db: Session, root: EmailMessage, step: int, steps: int) -> EmailMessage:
    """Write and send follow-up `step` (1-based) of this thread from the first email's inbox."""
    from app.services.conversations import add_message
    from app.services.tracking import new_rfc_message_id, open_pixel_html, track_links, tracks_opens, unsubscribe_url

    # Never twice: a follow-up step that is already sending / sent (worker restart, retry) is not sent again
    existing = (
        db.query(EmailMessage)
        .filter(
            EmailMessage.followup_of_id == root.id,
            EmailMessage.followup_step == step,
            EmailMessage.status.in_(["sending", "sent"]),
        )
        .first()
    )
    if existing:
        return existing
    settings = _settings(db, root.user_id)
    account = db.get(EmailAccount, root.email_account_id)
    lead = db.get(Lead, root.lead_id) if root.lead_id else None
    # The first email's Gmail account sends the follow-up, with that account's own Gmail signature
    sig_html, sig_text = account_signature(account) if _adds_signature(db, root) else ("", "")
    body = _write_body(db, root, step, steps, lead, settings, bool(sig_html))

    thread = _thread(db, root)
    chain = [m.rfc_message_id for m in thread if m.rfc_message_id]
    subject = root.subject if root.subject.lower().startswith("re:") else f"Re: {root.subject}"
    rfc_message_id = new_rfc_message_id(account.email_address)
    headers = {"Message-ID": rfc_message_id}
    if chain:
        headers["In-Reply-To"] = chain[-1]
        headers["References"] = " ".join(chain[-20:])
    unsubscribe = unsubscribe_url(root.user_id, root.recipient_email)
    from app.utils.greeting import apply_greeting_text

    first = _first_name(lead, root)
    body = apply_greeting_text(body, "" if first == "there" else first)
    html, text = append_signature(_paragraphs_html(body), body, sig_html, sig_text)
    html += f'<p style="font-size:12px;color:#666">Not interested? <a href="{unsubscribe}">Unsubscribe</a></p>'
    text += f"\n\nUnsubscribe: {unsubscribe}"

    message = EmailMessage(
        user_id=root.user_id,
        campaign_id=root.campaign_id,
        campaign_lead_id=root.campaign_lead_id,
        lead_id=root.lead_id,
        email_account_id=account.id,
        recipient_email=root.recipient_email,
        recipient_name=root.recipient_name,
        subject=subject,
        html_body=html,
        text_body=text,
        status="sending",
        rfc_message_id=rfc_message_id,
        followup_of_id=root.id,
        followup_step=step,
    )
    db.add(message)
    db.commit()
    try:
        provider_id = send_gmail_message(
            db,
            account,
            to=root.recipient_email,
            subject=subject,
            html_body=(
                track_links(html, message.id, skip=(unsubscribe,)) + open_pixel_html(message.id)
                if tracks_opens(db, root.user_id)
                else html
            ),
            text_body=text,
            headers=headers,
        )
    except GmailSendError as exc:
        message.status, message.error_message, message.error_kind = "failed", str(exc), exc.kind
        if not exc.retryable:
            root.followups_done_at = datetime.now(UTC)  # a permanent failure ends the thread's follow-ups
        db.commit()
        if exc.kind == "recipient":
            from app.services.account_health import suppress_refused_recipient

            suppress_refused_recipient(db, root.user_id, root.lead_id, root.recipient_email)
        raise
    now = datetime.now(UTC)
    message.status, message.provider_message_id, message.sent_at = "sent", provider_id, now
    if step >= steps:
        root.followups_done_at = now
    if lead:
        lead.last_contacted_at = now
    # Shown in the lead's conversation (with their reply, if one comes)
    add_message(
        db,
        root,
        direction="out",
        source="followup",
        from_email=account.email_address,
        to_email=root.recipient_email,
        subject=subject,
        body_text=body,
        rfc_message_id=rfc_message_id,
        occurred_at=now,
        email_message_id=message.id,
    )
    db.commit()
    logger.info("followup_sent", root_id=root.id, step=step, of=steps, lead_id=root.lead_id)
    return message


def due_followups(db: Session, user_id: str | None = None) -> list[tuple[EmailMessage, int, int]]:
    """(first email, next step, total steps) for every thread whose next follow-up is due now."""
    query = db.query(EmailMessage).filter(
        EmailMessage.followups_enabled.is_(True),
        EmailMessage.followups_done_at.is_(None),
        EmailMessage.followup_of_id.is_(None),
        EmailMessage.status == "sent",
        EmailMessage.sent_at.isnot(None),
        EmailMessage.is_test.is_(False),
    )
    if user_id:
        query = query.filter(EmailMessage.user_id == user_id)
    now = datetime.now(UTC)
    due, settings_cache = [], {}
    for root in query.order_by(EmailMessage.sent_at.asc()).limit(500):
        if root.user_id not in settings_cache:
            settings_cache[root.user_id] = _settings(db, root.user_id)
        settings = settings_cache[root.user_id]
        reason = _stop_reason(db, root, settings)
        days = followup_days(settings)
        thread = _thread(db, root)
        step = len(thread)  # 1 = the first follow-up
        if reason or step > len(days):
            root.followups_done_at = now
            db.commit()
            if reason:
                logger.info("followups_stopped", root_id=root.id, reason=reason)
            continue
        if thread[-1].sent_at + timedelta(days=days[step - 1]) <= now:
            due.append((root, step, len(days)))
    return due


def run_followups(db: Session) -> dict:
    """Send the follow-ups that are due: one per inbox per round, inside the sending hours, within daily limits."""
    sent = waiting = 0
    used: set[str] = set()
    for root, step, steps in due_followups(db):
        if root.email_account_id in used or _on_hold(db, root) or not _send_time_ok(db, root):
            waiting += 1
            continue
        account = db.get(EmailAccount, root.email_account_id)
        if not account_ready(account) or daily_limit_reached(db, account) or sent_recently(db, account):
            waiting += 1  # same inbox as the first email keeps the thread together: wait for it
            continue
        try:
            send_followup(db, root, step, steps)
            sent += 1
        except GmailSendError as exc:
            logger.warning("followup_send_failed", root_id=root.id, step=step, error=str(exc)[:200])
        except Exception as exc:  # one bad thread must not stop the others
            db.rollback()
            logger.error("followup_failed", root_id=root.id, step=step, error=str(exc)[:200])
        used.add(root.email_account_id)
    if sent:
        logger.info("followups_round", sent=sent, waiting=waiting)
    return {"sent": sent, "waiting": waiting}


def followup_summary(db: Session, root: EmailMessage) -> dict:
    """What happens next in this thread (shown on the lead page)."""
    settings = _settings(db, root.user_id)
    days = followup_days(settings)
    thread = _thread(db, root)
    step = len(thread)
    next_at = None
    if root.followups_enabled and not root.followups_done_at and step <= len(days):
        next_at = thread[-1].sent_at + timedelta(days=days[step - 1])
    return {
        "enabled": root.followups_enabled,
        "sent": step - 1,
        "total": len(days),
        "done": bool(root.followups_done_at),
        "next_at": next_at,
    }


def stop_followups(db: Session, user_id: str, lead_id: str) -> int:
    """Stop every pending follow-up to this lead (button on the lead page)."""
    now = datetime.now(UTC)
    roots = (
        db.query(EmailMessage)
        .filter(
            EmailMessage.user_id == user_id,
            EmailMessage.lead_id == lead_id,
            EmailMessage.followup_of_id.is_(None),
            EmailMessage.followups_done_at.is_(None),
        )
        .all()
    )
    for root in roots:
        root.followups_done_at = now
    db.commit()
    return len(roots)
