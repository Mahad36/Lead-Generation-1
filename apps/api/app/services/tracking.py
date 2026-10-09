"""Email open tracking and public unsubscribe links.

Clicks: links in the email body are rewritten to PUBLIC_URL/t/c/<signed message id + url>, which records the click
and redirects to the original address. The unsubscribe link is never rewritten.

Opens: a 1x1 transparent image served from PUBLIC_URL/t/o/<signed message id>.gif is appended to outgoing emails.
Mail apps that load images record an open. Apple Mail preloads images (false opens) and Outlook often blocks them
(missed opens), so an open means "probably read", not proof.

Only the /t/ routes need to be reachable from the internet (e.g. through a Cloudflare Tunnel); the rest of the app
stays on this machine.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta
from email.utils import make_msgid
from html import unescape

from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_config import get_logger
from app.models import EmailEvent, EmailMessage

logger = get_logger("tracking")

# Mail security scanners fetch images the moment a message arrives; a "read" this soon after sending isn't a person.
MIN_SECONDS_AFTER_SEND = 10

PIXEL_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00"
    b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def tracks_opens(db: Session, user_id: str) -> bool:
    """Whether sent emails carry the open-tracking image (Settings -> Sending). Emails never carry bulk-mail
    (List-Unsubscribe) headers: they mark 1-to-1 cold email as list mail for Gmail's Promotions tab."""
    from app.models import UserSettings

    settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    return bool(settings and settings.track_opens)


def public_url() -> str:
    return get_settings().public_url.strip().rstrip("/")


def _signature(message_id: str) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, f"open:{message_id}".encode(), hashlib.sha256).hexdigest()[:24]


def open_token(message_id: str) -> str:
    return f"{message_id}.{_signature(message_id)}"


def message_id_from_token(token: str) -> str | None:
    message_id, _, signature = token.partition(".")
    if message_id and signature and hmac.compare_digest(signature, _signature(message_id)):
        return message_id
    return None


def _click_signature(payload: str) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, f"click:{payload}".encode(), hashlib.sha256).hexdigest()[:24]


def click_token(message_id: str, url: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps([message_id, url]).encode()).decode().rstrip("=")
    return f"{payload}.{_click_signature(payload)}"


def click_target(token: str) -> tuple[str, str] | None:
    """(message_id, url) for a valid click token; None if it was tampered with (no open redirect)."""
    payload, _, signature = token.rpartition(".")
    if not payload or not hmac.compare_digest(signature, _click_signature(payload)):
        return None
    try:
        message_id, url = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, TypeError):
        return None
    if not str(url).startswith(("http://", "https://")):
        return None
    return str(message_id), str(url)


_HREF = re.compile(r"""href=(["'])(https?://[^"']+)\1""", re.IGNORECASE)


def track_links(html: str, message_id: str, *, skip: tuple[str, ...] = ()) -> str:
    """Route http(s) links through the click tracker. Links starting with any of `skip` are left as they are."""
    base = public_url()
    if not base or not get_settings().enable_email_click_tracking:
        return html

    def rewrite(match: re.Match) -> str:
        quote, href = match.group(1), match.group(2)
        url = unescape(href)
        if url.startswith(base) or any(url.startswith(s) for s in skip if s):
            return match.group(0)
        return f"href={quote}{base}/t/c/{click_token(message_id, url)}{quote}"

    return _HREF.sub(rewrite, html)


def open_pixel_html(message_id: str) -> str:
    base = public_url()
    if not base or not get_settings().enable_email_open_tracking:
        return ""
    # A plain image tag from your own domain: newsletter-style hiding (opacity 0, display tricks) is a bulk signal.
    return f'<img src="{base}/t/o/{open_token(message_id)}.gif" width="1" height="1" alt="" border="0">'


def unsubscribe_url(user_id: str, email: str) -> str:
    from app.utils.security import create_unsubscribe_token

    token = create_unsubscribe_token(user_id, email)
    base = public_url()
    return f"{base}/t/u/{token}" if base else f"{get_settings().frontend_url}/unsubscribe/{token}"


def new_rfc_message_id(sender: str) -> str:
    domain = sender.rpartition("@")[2] or None
    return make_msgid(domain=domain)


def record_open(db: Session, message_id: str, *, user_agent: str = "", ip: str = "") -> bool:
    """Returns True when the open was counted."""
    message = db.get(EmailMessage, message_id)
    if not message or not message.sent_at:
        return False
    now = datetime.now(UTC)
    counted = now - message.sent_at >= timedelta(seconds=MIN_SECONDS_AFTER_SEND)
    db.add(
        EmailEvent(
            message_id=message.id,
            event_type="open" if counted else "open_ignored",
            payload={"user_agent": user_agent[:300], "ip": ip[:64]},
            occurred_at=now,
        )
    )
    if counted:
        message.open_count = (message.open_count or 0) + 1
        message.opened_at = message.opened_at or now
        message.last_opened_at = now
    db.commit()
    if counted:
        logger.info("email_opened", message_id=message.id, count=message.open_count)
    return counted


def record_click(db: Session, message_id: str, url: str, *, user_agent: str = "", ip: str = "") -> bool:
    """Returns True when the click was counted (link scanners hit links right after delivery)."""
    message = db.get(EmailMessage, message_id)
    if not message or not message.sent_at:
        return False
    now = datetime.now(UTC)
    counted = now - message.sent_at >= timedelta(seconds=MIN_SECONDS_AFTER_SEND)
    db.add(
        EmailEvent(
            message_id=message.id,
            event_type="click" if counted else "click_ignored",
            payload={"url": url[:500], "user_agent": user_agent[:300], "ip": ip[:64]},
            occurred_at=now,
        )
    )
    if counted:
        message.click_count = (message.click_count or 0) + 1
        message.clicked_at = message.clicked_at or now
        message.last_clicked_at = now
        # A click implies the email was opened, even when images were blocked.
        message.opened_at = message.opened_at or now
    db.commit()
    if counted:
        logger.info("email_clicked", message_id=message.id, count=message.click_count)
    return counted


def tracking_summary(message: EmailMessage | None) -> dict:
    if not message:
        return {
            "open_count": 0, "opened_at": None, "last_opened_at": None, "click_count": 0, "clicked_at": None,
            "last_clicked_at": None, "replied_at": None, "bounced_at": None,
        }
    return {
        "open_count": message.open_count or 0,
        "opened_at": message.opened_at,
        "last_opened_at": message.last_opened_at,
        "click_count": message.click_count or 0,
        "clicked_at": message.clicked_at,
        "last_clicked_at": message.last_clicked_at,
        "replied_at": message.replied_at,
        "bounced_at": message.bounced_at,
    }
