"""Reply and bounce detection for SMTP email accounts, over IMAP with the same login (e.g. a Gmail App Password).

Every few minutes each connected SMTP account's INBOX is read (read-only; nothing is marked as read) for messages
that arrived since the last check:
- a reply: In-Reply-To / References name a message of one of our conversations, or it comes from someone we emailed;
- a bounce: from mailer-daemon / postmaster (or a delivery report) that mentions a Message-ID or address we sent to.
Replies mark the message and lead as replied and are saved with their text in the conversation
(services/conversations.py); bounces mark them bounced and add the address to the suppression list. The Sent
folder is read too, so answers you write in Gmail itself also appear in the conversation.
"""

from __future__ import annotations

import email
import imaplib
import re
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape

from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_config import get_logger
from app.models import EmailAccount, EmailEvent, EmailMessage, Lead
from app.services.gmail import _smtp_payload
from app.services.suppression import add_suppression
from app.utils.normalize import normalize_email

logger = get_logger("inbox")

LOOKBACK_DAYS = 30
FETCH_CHUNK = 200
IMAP_HOSTS = {
    "smtp.gmail.com": "imap.gmail.com",
    "smtp.googlemail.com": "imap.gmail.com",
    "smtp.office365.com": "outlook.office365.com",
    "smtp-mail.outlook.com": "outlook.office365.com",
    "smtp.mail.yahoo.com": "imap.mail.yahoo.com",
    "smtp.zoho.com": "imap.zoho.com",
}
BOUNCE_SENDERS = ("mailer-daemon", "postmaster")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MESSAGE_ID_RE = re.compile(r"<[^<>\s]+>")
FINAL_RECIPIENT_RE = re.compile(r"(?:final|original)-recipient:\s*rfc822;\s*<?([^\s<>;]+@[^\s<>;]+)", re.I)


def imap_host_for(data: dict) -> str:
    if data.get("imap_host"):
        return str(data["imap_host"])
    host = str(data.get("host") or "").lower()
    if host in IMAP_HOSTS:
        return IMAP_HOSTS[host]
    return "imap." + host[len("smtp."):] if host.startswith("smtp.") else ""


def _imap_date(value: datetime) -> str:
    # Built by hand: strftime("%b") follows the Windows locale, IMAP needs English month names.
    return f"{value.day:02d}-{MONTHS[value.month - 1]}-{value.year}"


def _parse_uid(meta: bytes) -> int | None:
    match = re.search(rb"UID (\d+)", meta)
    return int(match.group(1)) if match else None


def _fetch(imap: imaplib.IMAP4, uids: list[int], part: str) -> list[tuple[int, bytes]]:
    out: list[tuple[int, bytes]] = []
    for start in range(0, len(uids), FETCH_CHUNK):
        chunk = ",".join(str(u) for u in uids[start : start + FETCH_CHUNK])
        typ, data = imap.uid("FETCH", chunk, f"(UID {part})")
        if typ != "OK":
            continue
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2:
                uid = _parse_uid(item[0])
                if uid is not None:
                    out.append((uid, item[1]))
    return out


def _received_at(msg: email.message.Message) -> datetime:
    try:
        value = parsedate_to_datetime(msg.get("Date", ""))
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def _mark_replied(db: Session, message: EmailMessage, when: datetime, sender: str) -> None:
    if message.replied_at:
        return
    message.replied_at = when
    db.add(EmailEvent(message_id=message.id, event_type="reply", payload={"from": sender}, occurred_at=when))
    lead = db.get(Lead, message.lead_id) if message.lead_id else None
    if lead and lead.email_status != "bounced":
        lead.email_status = "replied"
    logger.info("email_replied", message_id=message.id, lead_id=message.lead_id)


# Reply-based opt-out (plain personal emails carry "just reply and I won't email you again" instead of a link).
OPT_OUT_RE = re.compile(
    r"\b(unsubscribe|remove me|take me off|opt[ -]?out|stop (?:emailing|contacting|sending)|"
    r"(?:do not|don'?t) (?:email|contact|message) (?:me|us)|not interested|no,? thanks|no thank you)\b",
    re.I,
)
QUOTE_START_RE = re.compile(r"^(?:On .+ wrote:|-----Original Message-----|From: .+)$", re.M)


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", "", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|blockquote)>", "\n", html)
    html = re.sub(r"(?is)<blockquote\b.*", "", html)  # the quoted original, in HTML-only replies
    text = unescape(re.sub(r"<[^>]+>", "", html)).replace("\xa0", " ")
    lines = [line.rstrip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _reply_text(raw: bytes) -> str:
    """The new text of a reply: plain-text body (or the HTML one as text) without the quoted original."""
    msg = email.message_from_bytes(raw)
    text = html = ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_filename():
            continue
        kind = part.get_content_type()
        if kind in ("text/plain", "text/html"):
            payload = part.get_payload(decode=True) or b""
            body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if kind == "text/plain" and not text:
                text = body
            elif kind == "text/html" and not html:
                html = body
    if not text.strip() and html:
        text = _html_to_text(html)
    match = QUOTE_START_RE.search(text)
    if match:
        text = text[: match.start()]
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))


def _honour_opt_out(db: Session, message: EmailMessage, text: str) -> bool:
    if not OPT_OUT_RE.search(text[:2000]):
        return False
    add_suppression(db, message.user_id, message.recipient_email, reason="replied_opt_out", source="inbox_check")
    logger.info("email_opt_out_reply", message_id=message.id, lead_id=message.lead_id)
    return True


def _mark_bounced(db: Session, message: EmailMessage, when: datetime) -> None:
    if message.bounced_at:
        return
    message.bounced_at, message.status = when, "bounced"
    db.add(EmailEvent(message_id=message.id, event_type="bounce", payload={}, occurred_at=when))
    lead = db.get(Lead, message.lead_id) if message.lead_id else None
    if lead:
        lead.email_status = "bounced"
    add_suppression(db, message.user_id, message.recipient_email, reason="bounced", source="inbox_check")
    logger.info("email_bounced", message_id=message.id, lead_id=message.lead_id)


def _find_sent_folder(imap: imaplib.IMAP4) -> str:
    """The Sent folder's name (Gmail: "[Gmail]/Sent Mail", localised elsewhere), from its \\Sent flag."""
    typ, folders = imap.list()
    if typ != "OK":
        return ""
    for raw in folders or []:
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        if "\\Sent" in line:
            match = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
            if match:
                return match.group(1)
    return ""


def _search_new(imap: imaplib.IMAP4, last_uid: int | None, same_folder: bool, since: datetime) -> list[int]:
    criteria = f"UID {last_uid + 1}:*" if same_folder and last_uid else f"SINCE {_imap_date(since - timedelta(days=1))}"
    typ, found = imap.uid("SEARCH", None, criteria)
    uids = sorted(int(u) for u in (found[0] or b"").split()) if typ == "OK" else []
    if same_folder and last_uid:
        uids = [u for u in uids if u > last_uid]  # "n:*" always returns the newest message
    return uids


def check_account(db: Session, account: EmailAccount) -> dict:
    """Read new INBOX (replies, bounces) and Sent folder (your answers from Gmail) messages for one SMTP account."""
    from app.services.conversations import add_message, reply_message_ids, thread_index

    data = _smtp_payload(account) or {}
    host = imap_host_for(data)
    if not host:
        return {"skipped": "no imap host"}
    since = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)
    answers = reply_message_ids(db, account.user_id)
    roots = [
        m
        for m in db.query(EmailMessage).filter(
            EmailMessage.email_account_id == account.id,
            EmailMessage.rfc_message_id != "",
            EmailMessage.sent_at >= since,
            EmailMessage.is_test.is_(False),
            EmailMessage.followup_of_id.is_(None),  # follow-ups belong to their first email's conversation
        )
        if m.id not in answers and not m.bounced_at
    ]
    if not roots:
        return {"skipped": "nothing to watch"}
    index = thread_index(db, roots)  # Message-ID of every message in these conversations -> outreach email
    by_recipient: dict[str, list[EmailMessage]] = {}
    for m in sorted(roots, key=lambda m: m.sent_at):
        by_recipient.setdefault(normalize_email(m.recipient_email), []).append(m)
    own = normalize_email(account.email_address)
    oldest = min(m.sent_at for m in roots)
    fields = (
        "BODY.PEEK[HEADER.FIELDS (FROM TO DATE SUBJECT MESSAGE-ID IN-REPLY-TO REFERENCES CONTENT-TYPE)]"
    )

    replies = bounces = opt_outs = answers_from_gmail = 0
    imap = imaplib.IMAP4_SSL(host, int(data.get("imap_port") or 993), timeout=30)
    try:
        imap.login(data.get("username") or account.email_address, data.get("password") or "")

        # ---- INBOX: replies and bounces
        typ, _ = imap.select("INBOX", readonly=True)
        if typ != "OK":
            raise RuntimeError("Could not open INBOX")
        uidvalidity = int((imap.response("UIDVALIDITY")[1] or [b"0"])[0] or 0)
        uids = _search_new(imap, account.inbox_last_uid, account.inbox_uidvalidity == uidvalidity, oldest)
        for uid, raw in _fetch(imap, uids, fields):
            msg = email.message_from_bytes(raw)
            sender = normalize_email(parseaddr(msg.get("From", ""))[1])
            if not sender or sender == own:
                continue
            when = _received_at(msg)
            local = sender.split("@")[0]
            if local in BOUNCE_SENDERS or msg.get_content_type() == "multipart/report":
                full = _fetch(imap, [uid], "BODY.PEEK[]")
                text = full[0][1].decode("utf-8", errors="replace") if full else ""
                hits = {m.id: m for rfc, m in index.items() if rfc in text and m.rfc_message_id == rfc}
                for addr in FINAL_RECIPIENT_RE.findall(text):
                    for m in by_recipient.get(normalize_email(addr), []):
                        hits[m.id] = m
                for m in hits.values():
                    if m.sent_at <= when + timedelta(minutes=5) and not m.bounced_at:
                        _mark_bounced(db, m, when)
                        bounces += 1
                continue
            refs = set(MESSAGE_ID_RE.findall(f"{msg.get('In-Reply-To', '')} {msg.get('References', '')}"))
            root = next((index[r] for r in refs if r in index), None)
            if root is None:
                # A fresh email (not a reply) from someone we contacted belongs to their latest conversation.
                earlier = [m for m in by_recipient.get(sender, []) if m.sent_at <= when + timedelta(minutes=5)]
                root = earlier[-1] if earlier else None
            if root is None:
                continue
            if not root.replied_at:
                _mark_replied(db, root, when, sender)
                replies += 1
            full = _fetch(imap, [uid], "BODY.PEEK[]")
            text = _reply_text(full[0][1]) if full else ""
            rfc = (msg.get("Message-ID") or "").strip() or f"<inbox-{account.id}-{uidvalidity}-{uid}@leadgen>"
            if add_message(db, root, direction="in", source="inbox", from_email=sender, to_email=own,
                           subject=_decoded(msg.get("Subject")), body_text=text, rfc_message_id=rfc, occurred_at=when):
                index[rfc] = root
            if _honour_opt_out(db, root, text):
                opt_outs += 1
        account.inbox_uidvalidity = uidvalidity
        if bounces:
            from app.services.deliverability import check_bounce_rate

            try:
                check_bounce_rate(db, account)  # too many bounces: pause this inbox until you resume it
            except Exception as exc:  # never let the pause check break reply / bounce detection
                logger.warning("bounce_rate_check_failed", account_id=account.id, error=str(exc)[:200])
        if uids:
            account.inbox_last_uid = max(uids[-1], account.inbox_last_uid or 0)
        elif account.inbox_last_uid is None:
            account.inbox_last_uid = 0

        # ---- Sent folder: answers you wrote in Gmail itself join the conversation
        folder = account.sent_folder or _find_sent_folder(imap)
        if folder and imap.select(f'"{folder}"', readonly=True)[0] == "OK":
            account.sent_folder = folder
            sent_validity = int((imap.response("UIDVALIDITY")[1] or [b"0"])[0] or 0)
            sent_uids = _search_new(imap, account.sent_last_uid, account.sent_uidvalidity == sent_validity, oldest)
            known = set(index) | {m.rfc_message_id for m in db.query(EmailMessage.rfc_message_id).filter(
                EmailMessage.email_account_id == account.id, EmailMessage.rfc_message_id != "")}
            for uid, raw in _fetch(imap, sent_uids, fields):
                msg = email.message_from_bytes(raw)
                rfc = (msg.get("Message-ID") or "").strip()
                if not rfc or rfc in known:
                    continue  # emails the app sent (outreach and in-app answers) are already recorded
                refs = set(MESSAGE_ID_RE.findall(f"{msg.get('In-Reply-To', '')} {msg.get('References', '')}"))
                root = next((index[r] for r in refs if r in index), None)
                if root is None:
                    continue
                full = _fetch(imap, [uid], "BODY.PEEK[]")
                if add_message(db, root, direction="out", source="gmail", from_email=own,
                               to_email=normalize_email(parseaddr(msg.get("To", ""))[1]),
                               subject=_decoded(msg.get("Subject")), body_text=_reply_text(full[0][1]) if full else "",
                               rfc_message_id=rfc, occurred_at=_received_at(msg)):
                    index[rfc] = root
                    answers_from_gmail += 1
            account.sent_uidvalidity = sent_validity
            if sent_uids:
                account.sent_last_uid = max(sent_uids[-1], account.sent_last_uid or 0)
            elif account.sent_last_uid is None:
                account.sent_last_uid = 0
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    account.inbox_checked_at, account.inbox_error = datetime.now(UTC), ""
    db.commit()
    return {"checked": len(uids), "replies": replies, "bounces": bounces, "opt_outs": opt_outs,
            "answers_from_gmail": answers_from_gmail}


def _decoded(value) -> str:
    """Subject header text (handles =?utf-8?...?= encoded words)."""
    from email.header import decode_header, make_header

    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return str(value or "")


def check_all_inboxes(db: Session) -> dict:
    accounts = db.query(EmailAccount).filter(EmailAccount.status == "connected").all()
    totals = {"accounts": 0, "replies": 0, "bounces": 0}
    for account in accounts:
        if not _smtp_payload(account):
            continue  # Gmail API accounts only have send permission
        try:
            result = check_account(db, account)
        except Exception as exc:  # one account's problem must not stop the others
            db.rollback()
            error = str(exc)[:240]
            if account.inbox_error != error:
                logger.warning("inbox_check_failed", account_id=account.id, error=error)
            account.inbox_error, account.inbox_checked_at = error, datetime.now(UTC)
            db.commit()
            continue
        if "skipped" not in result:
            totals["accounts"] += 1
            totals["replies"] += result["replies"]
            totals["bounces"] += result["bounces"]
    if totals["replies"] or totals["bounces"]:
        logger.info("inbox_checked", **totals)
    return totals


def inbox_check_enabled() -> bool:
    return get_settings().inbox_check_seconds > 0
