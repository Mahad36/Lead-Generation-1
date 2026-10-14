"""Each Gmail account's own signature, managed only in Gmail.

Gmail adds your signature only in its own compose window: emails sent over SMTP (App Password) or the Gmail API
never get it automatically. Reading the signature setting needs the Gmail API with an extra permission, which an
App Password can't use. So the app reads it from that same account's Sent folder: emails written in Gmail carry
the signature in a block Gmail marks `data-smartmail="gmail_signature"`. The latest one found is stored on that
account only, and the app adds it to that account's outgoing emails.

Each account only ever uses its own signature. When none is found, emails from that account go out without one
and Email setup / the campaign form show a warning. After changing a signature in Gmail, send one email from Gmail
(for example to yourself) and press Refresh in Email setup; the app also re-checks once a day.
"""

from __future__ import annotations

import email
import imaplib
import re
import threading
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models import EmailAccount
from app.services.gmail import _smtp_payload

logger = get_logger("gmail_signature")

CHECK_EVERY = timedelta(hours=24)
MAX_BODIES = 25  # emails written in Gmail opened per check, newest first
SIGNATURE_SELECTOR = '[data-smartmail="gmail_signature"], div.gmail_signature'


def extract_signature(html: str) -> tuple[str, str]:
    """(html, text) of the Gmail signature block in an email's HTML, or ("", "")."""
    soup = BeautifulSoup(html or "", "lxml")
    block = soup.select_one(SIGNATURE_SELECTOR)
    if not block:
        return "", ""
    for tag in block.find_all(["script", "style", "iframe", "object", "form"]):
        tag.decompose()
    for tag in block.find_all(True):
        for attr in list(tag.attrs):
            if attr.lower().startswith("on"):  # no inline event handlers
                del tag.attrs[attr]
    text = "\n".join(line.strip() for line in block.get_text("\n").splitlines() if line.strip())
    if not text and not block.find("img"):
        return "", ""
    return str(block), text


def drop_broken_images(html: str) -> tuple[str, list[str]]:
    """Remove signature images that don't load (e.g. a hosting link that expired), so leads never see empty boxes.

    Returns the cleaned HTML and the names (alt text or file name) of the images removed.
    """
    import httpx

    soup = BeautifulSoup(html, "lxml")
    block = soup.select_one(SIGNATURE_SELECTOR) or soup
    removed = []
    for img in block.find_all("img"):
        src = (img.get("src") or "").strip()
        ok = False
        if src.startswith(("http://", "https://")):
            try:
                with httpx.Client(timeout=10, follow_redirects=True) as client:
                    response = client.get(src, headers={"User-Agent": "Mozilla/5.0"})
                ok = response.status_code < 400 and len(response.content) > 0
            except Exception:
                ok = False
        if not ok:
            removed.append(img.get("alt") or src.rsplit("/", 1)[-1][:40] or "image")
            img.decompose()
    return str(block), removed


def _html_part(msg: email.message.Message) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/html" and not part.get_filename():
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def _composed_in_gmail(imap: imaplib.IMAP4) -> list[bytes]:
    """UIDs of sent emails written in Gmail itself (web / app), newest first."""
    typ, found = imap.uid("SEARCH", None, "ALL")
    uids = found[0].split() if typ == "OK" and found and found[0] else []
    picked = []
    for i in range(0, len(uids), 300):
        chunk = b",".join(uids[i : i + 300]).decode()
        typ, rows = imap.uid("FETCH", chunk, "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
        for row in rows or []:
            if isinstance(row, tuple) and b"mail.gmail.com" in row[1].lower():
                match = re.search(rb"UID (\d+)", row[0])
                if match:
                    picked.append(match.group(1))
    return sorted(picked, key=int, reverse=True)


def refresh_signature(db: Session, account: EmailAccount) -> dict:
    """Read this account's current Gmail signature from its own Sent folder and store it on the account."""
    from app.services.inbox import _find_sent_folder, imap_host_for

    now = datetime.now(UTC)
    data = _smtp_payload(account)
    if not data:
        account.gmail_signature_error = "Only Gmail accounts connected with an App Password can share their signature."
        account.gmail_signature_checked_at = now
        db.commit()
        return signature_info(account)
    html = text = ""
    found_at = None
    try:
        host = imap_host_for(data)
        imap = imaplib.IMAP4_SSL(host, int(data.get("imap_port") or 993), timeout=30)
        try:
            imap.login(data.get("username") or account.email_address, data.get("password") or "")
            folder = account.sent_folder or _find_sent_folder(imap)
            if not folder or imap.select(f'"{folder}"', readonly=True)[0] != "OK":
                raise RuntimeError("Couldn't open the Sent folder")
            for uid in _composed_in_gmail(imap)[:MAX_BODIES]:
                typ, rows = imap.uid("FETCH", uid.decode(), "(BODY.PEEK[])")
                raw = next((r[1] for r in rows or [] if isinstance(r, tuple)), b"")
                msg = email.message_from_bytes(raw)
                html, text = extract_signature(_html_part(msg))
                if html:
                    try:
                        found_at = parsedate_to_datetime(msg.get("Date", ""))
                    except (TypeError, ValueError):
                        found_at = None
                    break
        finally:
            try:
                imap.logout()
            except Exception:
                pass
    except Exception as exc:
        account.gmail_signature_error = f"Couldn't read the signature: {str(exc)[:200]}"
        account.gmail_signature_checked_at = now
        db.commit()
        logger.warning("gmail_signature_check_failed", account_id=account.id, error=str(exc)[:200])
        return signature_info(account)
    account.gmail_signature_checked_at = now
    if html:
        html, broken = drop_broken_images(html)
        account.gmail_signature_html, account.gmail_signature_text = html, text
        account.gmail_signature_from = found_at
        # Found, but with images that don't load: shown as a warning next to the signature
        account.gmail_signature_error = (
            f"{len(broken)} image(s) in this signature don't load ({', '.join(broken[:4])}), so they are left out. "
            "Fix the image links in Gmail's signature settings, send one email from Gmail, then press Refresh."
            if broken
            else ""
        )
    else:
        account.gmail_signature_html = account.gmail_signature_text = ""
        account.gmail_signature_from = None
        account.gmail_signature_error = (
            "No signature found in emails written in this Gmail. Set the signature in Gmail, send one email from "
            "Gmail (for example to yourself), then press Refresh."
        )
    db.commit()
    logger.info("gmail_signature_checked", account_id=account.id, found=bool(html))
    return signature_info(account)


def refresh_in_background(account_id: str) -> None:
    def run() -> None:
        from app.database import get_session

        db = get_session()
        try:
            account = db.get(EmailAccount, account_id)
            if account:
                refresh_signature(db, account)
        finally:
            db.close()

    threading.Thread(target=run, name="gmail-signature", daemon=True).start()


def refresh_due(db: Session) -> int:
    """Daily re-check, so a signature changed in Gmail is picked up without pressing Refresh."""
    cutoff = datetime.now(UTC) - CHECK_EVERY
    checked = 0
    for account in db.query(EmailAccount).filter(EmailAccount.status == "connected"):
        if not _smtp_payload(account):
            continue
        if account.gmail_signature_checked_at and account.gmail_signature_checked_at > cutoff:
            continue
        try:
            refresh_signature(db, account)
            checked += 1
        except Exception as exc:
            db.rollback()
            logger.warning("gmail_signature_refresh_failed", account_id=account.id, error=str(exc)[:200])
    return checked


def account_signature(account: EmailAccount | None) -> tuple[str, str]:
    """(html, text) to add under an email sent from this account, in Gmail's own layout. ("", "") when none."""
    if not account or not account.gmail_signature_html:
        return "", ""
    return f'<br clear="all"><br>{account.gmail_signature_html}', account.gmail_signature_text


def signature_info(account: EmailAccount) -> dict:
    return {
        "found": bool(account.gmail_signature_html),
        "html": account.gmail_signature_html,
        "text": account.gmail_signature_text,
        "from_email_dated": account.gmail_signature_from,
        "checked_at": account.gmail_signature_checked_at,
        "error": account.gmail_signature_error,
    }
