"""Conversations after an outreach email: lead replies, and your answers sent from the app (or from Gmail).

The outreach EmailMessage is the root of a conversation. Replies are saved by the inbox checker (services/inbox.py),
which also picks up answers you write in Gmail from the Sent folder. Answers sent from the app go out from the same
mailbox as a real reply ("Re: ...", In-Reply-To / References, earlier message quoted) so they thread in the lead's
mail app, and they are EmailMessages too: open tracking and the daily send limit apply.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models import ConversationMessage, EmailAccount, EmailMessage, Lead, UserSettings
from app.services.gmail import GmailSendError, daily_limit_reached, send_gmail_message
from app.services.tracking import new_rfc_message_id, open_pixel_html, tracking_summary, tracks_opens
from app.services.gmail_signature import account_signature
from app.utils.signature import append_signature

logger = get_logger("conversations")


# ---------------------------------------------------------------- threading (used by the inbox checker)


def reply_message_ids(db: Session, user_id: str) -> set[str]:
    """EmailMessages that are answers inside a conversation (not outreach roots)."""
    return {
        row[0]
        for row in db.query(ConversationMessage.email_message_id).filter(
            ConversationMessage.user_id == user_id, ConversationMessage.email_message_id.isnot(None)
        )
    }


def thread_index(db: Session, roots: list[EmailMessage]) -> dict[str, EmailMessage]:
    """Every Message-ID in these conversations (the outreach email and all messages after it) -> its root."""
    index = {root.rfc_message_id: root for root in roots if root.rfc_message_id}
    by_id = {root.id: root for root in roots}
    if by_id:
        for root_id, rfc in db.query(ConversationMessage.root_message_id, ConversationMessage.rfc_message_id).filter(
            ConversationMessage.root_message_id.in_(list(by_id))
        ):
            index[rfc] = by_id[root_id]
    return index


def add_message(
    db: Session,
    root: EmailMessage,
    *,
    direction: str,
    source: str,
    from_email: str,
    to_email: str,
    subject: str,
    body_text: str,
    rfc_message_id: str,
    occurred_at: datetime,
    email_message_id: str | None = None,
) -> ConversationMessage | None:
    """Save one message in the conversation (once: a Message-ID seen again is ignored)."""
    exists = (
        db.query(ConversationMessage.id)
        .filter(ConversationMessage.user_id == root.user_id, ConversationMessage.rfc_message_id == rfc_message_id)
        .first()
    )
    if exists:
        return None
    item = ConversationMessage(
        user_id=root.user_id,
        root_message_id=root.id,
        lead_id=root.lead_id,
        email_account_id=root.email_account_id,
        email_message_id=email_message_id,
        direction=direction,
        source=source,
        from_email=from_email[:320],
        to_email=to_email[:320],
        subject=subject[:500],
        body_text=body_text[:20000],
        rfc_message_id=rfc_message_id[:255],
        occurred_at=occurred_at,
        read_at=None if direction == "in" else occurred_at,
    )
    db.add(item)
    db.flush()
    return item


# ---------------------------------------------------------------- reading


def _root(db: Session, user_id: str, root_id: str) -> EmailMessage:
    root = db.query(EmailMessage).filter(EmailMessage.id == root_id, EmailMessage.user_id == user_id).first()
    if not root:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return root


def _snippet(text: str, limit: int = 140) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "..."


def list_threads(db: Session, user_id: str, *, unread_only: bool = False, lead_id: str | None = None) -> list[dict]:
    """Conversations with at least one reply, newest activity first."""
    query = db.query(ConversationMessage).filter(ConversationMessage.user_id == user_id)
    if lead_id:
        query = query.filter(ConversationMessage.lead_id == lead_id)
    messages = query.order_by(ConversationMessage.occurred_at.asc()).all()
    grouped: dict[str, list[ConversationMessage]] = {}
    for message in messages:
        grouped.setdefault(message.root_message_id, []).append(message)
    roots = {r.id: r for r in db.query(EmailMessage).filter(EmailMessage.id.in_(list(grouped)))} if grouped else {}
    leads = {}
    lead_ids = {r.lead_id for r in roots.values() if r.lead_id}
    if lead_ids:
        leads = {lead.id: lead for lead in db.query(Lead).filter(Lead.id.in_(lead_ids))}
    threads = []
    for root_id, items in grouped.items():
        root = roots.get(root_id)
        if not root or not any(m.direction == "in" for m in items):
            continue
        unread = sum(1 for m in items if m.direction == "in" and not m.read_at)
        if unread_only and not unread:
            continue
        last = items[-1]
        lead = leads.get(root.lead_id) if root.lead_id else None
        threads.append(
            {
                "root_id": root.id,
                "lead_id": root.lead_id,
                "business_name": lead.business_name if lead else root.recipient_name,
                "contact_name": lead.contact_name if lead else "",
                "email": root.recipient_email,
                "website_url": lead.website_url if lead else "",
                "subject": root.subject,
                "last_at": last.occurred_at,
                "last_direction": last.direction,
                "last_snippet": _snippet(last.body_text),
                "unread": unread,
                "messages": len(items) + 1,
            }
        )
    threads.sort(key=lambda t: t["last_at"], reverse=True)
    return threads


def unread_count(db: Session, user_id: str) -> int:
    return (
        db.query(func.count(ConversationMessage.id))
        .filter(
            ConversationMessage.user_id == user_id,
            ConversationMessage.direction == "in",
            ConversationMessage.read_at.is_(None),
        )
        .scalar()
        or 0
    )


def get_thread(db: Session, user_id: str, root_id: str) -> dict:
    root = _root(db, user_id, root_id)
    lead = db.get(Lead, root.lead_id) if root.lead_id else None
    account = db.get(EmailAccount, root.email_account_id) if root.email_account_id else None
    items = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.root_message_id == root.id)
        .order_by(ConversationMessage.occurred_at.asc())
        .all()
    )
    sent_copies = {
        m.id: m
        for m in db.query(EmailMessage).filter(EmailMessage.id.in_([i.email_message_id for i in items if i.email_message_id]))
    } if any(i.email_message_id for i in items) else {}
    from app.services.website_audit import _html_to_text

    messages = [
        {
            "id": root.id,
            "direction": "out",
            "source": "outreach",
            "from_email": account.email_address if account else "",
            "to_email": root.recipient_email,
            "subject": root.subject,
            "body_text": _strip_footer(root.text_body or _html_to_text(root.html_body)),
            "at": root.sent_at or root.created_at,
            "read": True,
            "attachment": "Website audit PDF" if root.campaign_id is None else "",
            **tracking_summary(root),
        }
    ]
    for item in items:
        copy = sent_copies.get(item.email_message_id) if item.email_message_id else None
        messages.append(
            {
                "id": item.id,
                "direction": item.direction,
                "source": item.source,
                "from_email": item.from_email,
                "to_email": item.to_email,
                "subject": item.subject,
                "body_text": item.body_text,
                "at": item.occurred_at,
                "read": bool(item.read_at),
                "attachment": "",
                **tracking_summary(copy),
            }
        )
    return {
        "root_id": root.id,
        "lead_id": root.lead_id,
        "business_name": lead.business_name if lead else root.recipient_name,
        "contact_name": lead.contact_name if lead else "",
        "email": root.recipient_email,
        "website_url": lead.website_url if lead else "",
        "subject": root.subject,
        "messages": messages,
    }


def _strip_footer(text: str) -> str:
    """The outreach email's unsubscribe line isn't part of the conversation."""
    return text.split("\n\nUnsubscribe: ")[0].strip()


def mark_read(db: Session, user_id: str, root_id: str) -> None:
    root = _root(db, user_id, root_id)
    now = datetime.now(UTC)
    db.query(ConversationMessage).filter(
        ConversationMessage.root_message_id == root.id,
        ConversationMessage.direction == "in",
        ConversationMessage.read_at.is_(None),
    ).update({ConversationMessage.read_at: now})
    db.commit()


# ---------------------------------------------------------------- replying from the app


def _quoted(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in (text or "").strip().splitlines())


def send_reply(db: Session, user_id: str, root_id: str, body: str) -> dict:
    """Answer the lead from the app: a real reply in the same thread, with open tracking."""
    root = _root(db, user_id, root_id)
    body = (body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Write your reply first.")
    account = db.get(EmailAccount, root.email_account_id) if root.email_account_id else None
    if not account or account.status != "connected":
        raise HTTPException(status_code=400, detail="The email account this conversation started from isn't connected.")
    if daily_limit_reached(db, account):
        raise HTTPException(
            status_code=429,
            detail=f"Daily sending limit reached for {account.email_address}. It resets at midnight; "
                   "change it in AI & settings -> Sending.",
        )
    items = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.root_message_id == root.id)
        .order_by(ConversationMessage.occurred_at.asc())
        .all()
    )
    last_in = next((m for m in reversed(items) if m.direction == "in"), None)
    to = (last_in.from_email if last_in else "") or root.recipient_email
    # Gmail only threads messages whose subjects match, so answer under THEIR subject ("Thank You Re: ..." too),
    # like Gmail's own Reply button does.
    base = (last_in.subject if last_in and last_in.subject.strip() else root.subject).strip()
    subject = base if base.lower().startswith("re:") else f"Re: {base}"
    chain = [root.rfc_message_id] + [m.rfc_message_id for m in items]
    chain = [rfc for rfc in chain if rfc]
    rfc_message_id = new_rfc_message_id(account.email_address)
    headers = {"Message-ID": rfc_message_id}
    if chain:
        headers["In-Reply-To"] = chain[-1]
        headers["References"] = " ".join(chain[-20:])

    quoted_text = ""
    quoted_html = ""
    if last_in:
        when = last_in.occurred_at.strftime("%a, %d %b %Y at %H:%M")
        quoted_text = f"\n\nOn {when}, {last_in.from_email} wrote:\n{_quoted(last_in.body_text)}"
        quoted_html = (
            f'<p style="color:#666">On {escape(when)}, {escape(last_in.from_email)} wrote:</p>'
            f'<blockquote style="margin:0 0 0 .8ex;border-left:1px solid #ccc;padding-left:1ex;color:#555">'
            + "".join(f"<p>{escape(p)}</p>" for p in last_in.body_text.strip().split("\n\n") if p.strip())
            + "</blockquote>"
        )
    html_reply = "".join(f"<p>{escape(p).replace(chr(10), '<br>')}</p>" for p in body.split("\n\n") if p.strip())
    # This Gmail's own signature goes under your answer and above their quoted message, as in Gmail.
    sig_html, sig_text = account_signature(account)
    html_reply, signed_text = append_signature(html_reply, body, sig_html, sig_text)

    message = EmailMessage(
        user_id=user_id,
        lead_id=root.lead_id,
        email_account_id=account.id,
        recipient_email=to,
        recipient_name=root.recipient_name,
        subject=subject,
        html_body=html_reply + quoted_html,
        text_body=signed_text + quoted_text,
        status="sending",
        rfc_message_id=rfc_message_id,
    )
    db.add(message)
    db.commit()
    try:
        provider_id = send_gmail_message(
            db,
            account,
            to=to,
            subject=subject,
            html_body=html_reply + quoted_html + (open_pixel_html(message.id) if tracks_opens(db, user_id) else ""),
            text_body=signed_text + quoted_text,
            headers=headers,
        )
    except GmailSendError as exc:
        message.status, message.error_message, message.error_kind = "failed", str(exc), exc.kind
        db.commit()
        raise HTTPException(status_code=502, detail=f"The reply was not sent: {exc}") from exc
    now = datetime.now(UTC)
    message.status, message.provider_message_id, message.sent_at = "sent", provider_id, now
    add_message(
        db,
        root,
        direction="out",
        source="app",
        from_email=account.email_address,
        to_email=to,
        subject=subject,
        body_text=body,
        rfc_message_id=rfc_message_id,
        occurred_at=now,
        email_message_id=message.id,
    )
    if last_in:
        mark_read(db, user_id, root.id)
    db.commit()
    logger.info("conversation_reply_sent", root_id=root.id, lead_id=root.lead_id)
    return get_thread(db, user_id, root.id)


def replied_leads(db: Session, user_id: str, q: str | None = None) -> list[dict]:
    """Every lead who answered one of your emails, latest reply first."""
    from app.services.leads import lead_file_info

    replies = (
        db.query(ConversationMessage)
        .filter(
            ConversationMessage.user_id == user_id,
            ConversationMessage.direction == "in",
        )
        .order_by(ConversationMessage.occurred_at.asc())
        .all()
    )
    roots = {r.id: r for r in db.query(EmailMessage).filter(EmailMessage.id.in_({m.root_message_id for m in replies}))}
    by_lead: dict[str, list[ConversationMessage]] = {}
    for m in replies:
        root = roots.get(m.root_message_id)
        key = m.lead_id or (root.lead_id if root else None) or f"email:{m.from_email}"
        by_lead.setdefault(key, []).append(m)
    # Replies found before conversations were saved: the outreach email is marked replied, but has no messages.
    for root in db.query(EmailMessage).filter(
        EmailMessage.user_id == user_id, EmailMessage.replied_at.isnot(None), EmailMessage.lead_id.isnot(None)
    ):
        by_lead.setdefault(root.lead_id, [])
        roots.setdefault(root.id, root)
    lead_ids = [k for k in by_lead if not k.startswith("email:")]
    leads = {lead.id: lead for lead in db.query(Lead).filter(Lead.user_id == user_id, Lead.id.in_(lead_ids))}
    # Your latest message per conversation, to show whose turn it is.
    last_out: dict[str, datetime] = {}
    for root_id, at in (
        db.query(ConversationMessage.root_message_id, func.max(ConversationMessage.occurred_at))
        .filter(ConversationMessage.user_id == user_id, ConversationMessage.direction == "out")
        .group_by(ConversationMessage.root_message_id)
    ):
        last_out[root_id] = at

    needle = (q or "").strip().lower()
    rows = []
    for key, items in by_lead.items():
        lead = leads.get(key)
        if lead is None and not key.startswith("email:"):
            continue  # the lead was deleted
        lead_roots =[r for r in roots.values() if lead and r.lead_id == lead.id]
        if items:
            first, last = items[0], items[-1]
            root_id = last.root_message_id
            first_at, last_at = first.occurred_at, last.occurred_at
            snippet, email = _snippet(last.body_text), last.from_email
        else:
            replied = sorted((r for r in lead_roots if r.replied_at), key=lambda r: r.replied_at)
            if not replied:
                continue
            root_id, first_at, last_at = replied[-1].id, replied[0].replied_at, replied[-1].replied_at
            snippet, email = "", replied[-1].recipient_email
        root = roots.get(root_id)
        you_last = last_out.get(root_id)
        row = {
            "lead_id": lead.id if lead else None,
            "root_id": root_id if items else None,
            "business_name": (lead.business_name if lead else "") or (root.recipient_name if root else "") or email,
            "contact_name": lead.contact_name if lead else "",
            "email": (lead.primary_email if lead else "") or email,
            "phone": lead.primary_phone if lead else "",
            "website_url": lead.website_url if lead else "",
            "category": lead.category if lead else "",
            "city": lead.city if lead else "",
            "country": lead.country if lead else "",
            "file_name": lead_file_info(db, lead)["file_name"] if lead else "",
            "subject": root.subject if root else "",
            "first_replied_at": first_at,
            "last_reply_at": last_at,
            "last_reply_snippet": snippet,
            "replies": len(items),
            "unread": sum(1 for m in items if not m.read_at),
            "you_answered": bool(you_last and you_last >= last_at),
            "your_last_answer_at": you_last,
        }
        if needle and needle not in " ".join(
            str(row[k] or "") for k in ("business_name", "contact_name", "email", "website_url", "city", "category")
        ).lower():
            continue
        rows.append(row)
    rows.sort(key=lambda r: r["last_reply_at"], reverse=True)
    return rows
