from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models import EmailAccount, EmailMessage
from app.utils.dates import local_day_start, local_today
from app.services.integrations import GoogleOAuthConfig, resolve_google_oauth
from app.utils.crypto import decrypt_text, encrypt_text

logger = get_logger("gmail")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GmailNotConfiguredError(RuntimeError):
    pass


@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    content: bytes
    subtype: str = "pdf"


class GmailSendError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None, kind: str = ""):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        # account_health.classify_send_error: temporary | rate_limited | quota | auth | recipient | rejected | other
        self.kind = kind or ("temporary" if retryable else "other")


def oauth_scopes() -> list[str]:
    from app.config import get_settings

    return get_settings().google_scopes_list


def require_google_oauth(db: Session, user_id: str) -> GoogleOAuthConfig:
    cfg = resolve_google_oauth(db, user_id)
    if not cfg.client_id or not cfg.client_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Add your Google OAuth Client ID and Client Secret in Settings before connecting Gmail.",
        )
    return cfg


def _configured_redirect_uri() -> str:
    """GOOGLE_REDIRECT_URI when explicitly set (e.g. the public Codespaces URL), else ''."""
    from app.config import get_settings

    settings = get_settings()
    if "google_redirect_uri" in settings.model_fields_set and settings.google_redirect_uri.strip():
        return settings.google_redirect_uri.strip()
    return ""


def oauth_redirect_uri(request) -> str:
    from app.config import get_settings

    # Behind a proxy (Codespaces, the Next.js rewrite) the request URL is internal, so an
    # explicitly configured public redirect URI must win.
    configured = _configured_redirect_uri()
    if configured:
        return configured
    base = str(request.base_url).rstrip("/")
    prefix = get_settings().api_prefix.rstrip("/")
    return f"{base}{prefix}/email-accounts/gmail/callback"


def suggested_redirect_uris() -> list[str]:
    from app.config import get_settings

    configured = _configured_redirect_uri()
    if configured:
        return [configured]
    settings = get_settings()
    path = f"{settings.api_prefix.rstrip('/')}/email-accounts/gmail/callback"
    hosts = []
    for raw in (settings.backend_url, settings.google_redirect_uri, "http://localhost:8003", "http://127.0.0.1:8003"):
        host = raw.split("/api/")[0].rstrip("/") if raw else ""
        if host and host not in hosts:
            hosts.append(host)
    return [f"{host}{path}" for host in hosts]


def build_authorization_url(db: Session, user_id: str, state: str, redirect_uri: str) -> str:
    cfg = require_google_oauth(db, user_id)
    scopes = oauth_scopes()
    params = {
        "client_id": cfg.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def exchange_code(db: Session, user_id: str, code: str, redirect_uri: str) -> dict:
    cfg = require_google_oauth(db, user_id)
    data = {
        "code": code,
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    response = httpx.post(GOOGLE_TOKEN_URL, data=data, timeout=30)
    if response.status_code >= 400:
        logger.error("oauth_exchange_failed", status=response.status_code)
        raise HTTPException(status_code=400, detail="Google authorization failed")
    return response.json()


def fetch_user_email(access_token: str) -> tuple[str, str]:
    response = httpx.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("email") or "", payload.get("name") or ""


def store_account(db: Session, user_id: str, token_payload: dict) -> EmailAccount:
    access_token = token_payload.get("access_token") or ""
    email, name = fetch_user_email(access_token)
    if not email:
        raise HTTPException(status_code=400, detail="Unable to read Gmail address")
    cfg = resolve_google_oauth(db, user_id)
    scopes = oauth_scopes()
    credentials = {
        "token": access_token,
        "refresh_token": token_payload.get("refresh_token"),
        "token_uri": GOOGLE_TOKEN_URL,
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "scopes": scopes,
        "expiry": None,
    }
    existing = (
        db.query(EmailAccount)
        .filter(EmailAccount.user_id == user_id, EmailAccount.email_address == email)
        .first()
    )
    account = existing or EmailAccount(
        user_id=user_id, email_address=email, provider="gmail", ramp_started_on=local_today()
    )
    if existing and existing.encrypted_credentials:
        try:
            previous = json.loads(decrypt_text(existing.encrypted_credentials))
            if not credentials.get("refresh_token") and previous.get("refresh_token"):
                credentials["refresh_token"] = previous["refresh_token"]
        except Exception:
            pass
    account.display_name = name
    account.status = "connected"
    account.encrypted_credentials = encrypt_text(json.dumps(credentials))
    account.scopes = " ".join(scopes)
    account.last_error = ""
    db.add(account)
    db.commit()
    db.refresh(account)
    logger.info("gmail_connected", user_id=user_id, account_id=account.id)
    return account


def _credentials_from_account(account: EmailAccount) -> Credentials:
    raw = json.loads(decrypt_text(account.encrypted_credentials))
    creds = Credentials(
        token=raw.get("token"),
        refresh_token=raw.get("refresh_token"),
        token_uri=raw.get("token_uri") or GOOGLE_TOKEN_URL,
        client_id=raw.get("client_id"),
        client_secret=raw.get("client_secret"),
        scopes=raw.get("scopes"),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def persist_refreshed_credentials(db: Session, account: EmailAccount, creds: Credentials) -> None:
    payload = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or []),
    }
    account.encrypted_credentials = encrypt_text(json.dumps(payload))
    account.status = "connected"
    db.add(account)


def _build_message(
    *,
    sender: str,
    to: str,
    subject: str,
    html_body: str,
    text_body: str = "",
    attachments: list[EmailAttachment] | None = None,
    headers: dict[str, str] | None = None,
):
    if text_body:
        body = MIMEMultipart("alternative")
        body.attach(MIMEText(text_body, "plain", "utf-8"))
        body.attach(MIMEText(html_body, "html", "utf-8"))
    else:
        body = MIMEText(html_body, "html", "utf-8")
    if attachments:
        message = MIMEMultipart("mixed")
        message.attach(body)
        for item in attachments:
            part = MIMEApplication(item.content, _subtype=item.subtype)
            part.add_header("Content-Disposition", "attachment", filename=item.filename)
            message.attach(part)
    else:
        message = body
    message["to"] = to
    message["from"] = sender
    message["subject"] = subject
    for name, value in (headers or {}).items():
        message[name] = value
    return message


def build_mime(
    *,
    sender: str,
    to: str,
    subject: str,
    html_body: str,
    text_body: str = "",
    attachments: list[EmailAttachment] | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    message = _build_message(
        sender=sender,
        to=to,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        attachments=attachments,
        headers=headers,
    )
    return base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")


def _smtp_payload(account: EmailAccount) -> dict | None:
    if not account.encrypted_credentials:
        return None
    try:
        data = json.loads(decrypt_text(account.encrypted_credentials))
    except Exception:
        return None
    if account.provider == "smtp" or data.get("type") == "smtp":
        return data
    return None


def verify_smtp_login(host: str, port: int, username: str, password: str, use_tls: bool = True) -> None:
    import smtplib

    host = (host or "").strip()
    username = (username or "").strip()
    port = int(port or 587)
    if not host or not username or not password:
        raise GmailSendError("SMTP host, username, and password are required", retryable=False)
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
                smtp.login(username, password)
            return
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls()
                smtp.ehlo()
            smtp.login(username, password)
    except Exception as exc:
        raise GmailSendError(f"SMTP connection failed: {str(exc)[:200]}", retryable=False) from exc


def store_smtp_account(
    db: Session,
    user_id: str,
    *,
    email_address: str,
    host: str,
    port: int,
    username: str,
    password: str,
    use_tls: bool = True,
    display_name: str = "",
) -> EmailAccount:
    address = email_address.strip().lower()
    user_name = (username or address).strip()
    if "gmail" in host.lower() or "googlemail" in host.lower():
        password = password.replace(" ", "")  # App Passwords are shown as "abcd efgh ijkl mnop"
    verify_smtp_login(host, port, user_name, password, use_tls)
    payload = {
        "type": "smtp",
        "host": host.strip(),
        "port": int(port or 587),
        "username": user_name,
        "password": password,
        "use_tls": bool(use_tls),
    }
    account = (
        db.query(EmailAccount)
        .filter(EmailAccount.user_id == user_id, EmailAccount.email_address == address)
        .first()
    )
    if not account:
        # A new inbox ramps up: a few emails on day one, more each day (services/deliverability.py)
        account = EmailAccount(user_id=user_id, email_address=address, ramp_started_on=local_today())
    account.provider = "smtp"
    account.display_name = display_name or email_address
    account.status = "connected"
    account.encrypted_credentials = encrypt_text(json.dumps(payload))
    account.last_error = ""
    from app.models import UserSettings

    settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    if settings and settings.default_daily_send_limit:
        account.daily_limit = settings.default_daily_send_limit
    has_default = (
        db.query(EmailAccount)
        .filter(EmailAccount.user_id == user_id, EmailAccount.is_default.is_(True), EmailAccount.id != getattr(account, "id", None))
        .first()
    )
    if not has_default:
        account.is_default = True
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def send_smtp_message(
    db: Session,
    account: EmailAccount,
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str = "",
    attachments: list[EmailAttachment] | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    import smtplib

    data = _smtp_payload(account)
    if not data:
        raise GmailSendError("SMTP account is missing credentials", retryable=False)
    # The envelope: To plus any CC'd business contacts (the Cc header is what the recipients see)
    recipients = [to] + [a.strip() for a in ((headers or {}).get("Cc") or "").split(",") if a.strip()]
    message = _build_message(
        sender=sender_header(account),
        to=to,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        attachments=attachments,
        headers=headers,
    )
    try:
        port = int(data.get("port") or 587)
        username = data.get("username") or account.email_address
        password = data.get("password") or ""
        if port == 465:
            with smtplib.SMTP_SSL(data["host"], port, timeout=30) as smtp:
                smtp.login(username, password)
                smtp.sendmail(account.email_address, recipients, message.as_string())
        else:
            with smtplib.SMTP(data["host"], port, timeout=30) as smtp:
                smtp.ehlo()
                if data.get("use_tls", True):
                    smtp.starttls()
                    smtp.ehlo()
                smtp.login(username, password)
                smtp.sendmail(account.email_address, recipients, message.as_string())
        account.last_successful_send_at = datetime.now(UTC)
        account.last_error = ""
        account.status = "connected"
        from app.services.account_health import record_send_success

        record_send_success(db, account)
        db.add(account)
        db.commit()
        return f"smtp:{datetime.now(UTC).timestamp()}"
    except Exception as exc:
        from app.services.account_health import RETRYABLE, classify_send_error, record_send_failure

        kind, code = classify_send_error(exc)
        message = _smtp_error_text(exc, kind, code)
        account.last_error = message[:240]
        db.add(account)
        db.commit()
        record_send_failure(db, account, kind, f"code {code}" if code else kind)
        logger.error("smtp_send_failed", account_id=account.id, kind=kind, code=code, error=str(exc)[:300])
        raise GmailSendError(message[:240], retryable=kind in RETRYABLE, status_code=code, kind=kind) from exc


def _smtp_error_text(exc: Exception, kind: str = "", code: int | None = None) -> str:
    """A readable reason instead of the raw SMTP tuple (e.g. Gmail's "421 4.4.5 Server busy")."""
    if not kind:
        from app.services.account_health import classify_send_error

        kind, code = classify_send_error(exc)
    tag = f" (code {code})" if code else ""
    return {
        "temporary": f"Gmail was busy and asked to try again later{tag}. Retried automatically with increasing waits.",
        "rate_limited": f"Gmail is rate-limiting this account{tag}. Retried automatically after a longer wait.",
        "quota": f"Gmail says this account reached its daily sending limit{tag}. It pauses for 24 hours.",
        "auth": "Gmail rejected the login. Replace the App Password in Email setup.",
        "recipient": f"The recipient address doesn't exist or refused mail{tag}. Not retried; the address is suppressed.",
        "rejected": f"Gmail blocked this message as against its policy{tag}. Not retried; the account slows down.",
    }.get(kind, str(exc))


def sender_header(account: EmailAccount) -> str:
    """"Name <address>" when the account has a display name; a bare address looks automated."""
    from email.utils import formataddr

    name = (account.display_name or "").strip()
    return formataddr((name, account.email_address)) if name and "@" not in name else account.email_address


def sent_today_count(db: Session, account: EmailAccount) -> int:
    """Real emails this account sent today (UTC), whatever sent them: audits, campaigns, manual sends."""
    start = local_day_start()  # the user's midnight
    return (
        db.query(func.count(EmailMessage.id))
        .filter(
            EmailMessage.email_account_id == account.id,
            EmailMessage.sent_at >= start,
            EmailMessage.is_test.is_(False),
        )
        .scalar()
        or 0
    )


def daily_limit_reached(db: Session, account: EmailAccount) -> bool:
    """Today's limit reached: your limit, lower while ramping up or when the account's health calls for it."""
    from app.services.deliverability import effective_daily_limit

    if account.recommended_limit is not None and account.recommended_limit <= 0:
        return True  # paused by its health status
    limit = effective_daily_limit(account)
    return bool(limit) and sent_today_count(db, account) >= limit


def send_gmail_message(
    db: Session,
    account: EmailAccount,
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str = "",
    attachments: list[EmailAttachment] | None = None,
    headers: dict[str, str] | None = None,
    cc: list[str] | None = None,
) -> str:
    if account.status == "disconnected":
        raise GmailSendError("Email account is disconnected", retryable=False)
    cc = [a for a in dict.fromkeys(x.strip().lower() for x in (cc or [])) if a and a != to.strip().lower()]
    if cc:
        headers = {**(headers or {}), "Cc": ", ".join(cc)}  # visible to all recipients, as in Gmail
    if _smtp_payload(account):
        return send_smtp_message(
            db,
            account,
            to=to,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            attachments=attachments,
            headers=headers,
        )
    try:
        creds = _credentials_from_account(account)
        persist_refreshed_credentials(db, account, creds)
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        raw = build_mime(
            sender=sender_header(account),
            to=to,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            attachments=attachments,
            headers=headers,
        )
        sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        account.last_successful_send_at = datetime.now(UTC)
        account.last_error = ""
        db.add(account)
        db.commit()
        return sent.get("id") or ""
    except HttpError as exc:
        status_code = getattr(exc.resp, "status", None)
        retryable = status_code in {429, 500, 502, 503, 504}
        message = "Gmail API error"
        if status_code == 401:
            account.status = "expired"
            message = "Gmail authentication expired. Reconnect the account."
            retryable = False
        elif status_code == 429:
            message = "Gmail rate limit reached"
        elif status_code == 400:
            message = "Invalid recipient or message"
            retryable = False
        account.last_error = message
        db.add(account)
        db.commit()
        logger.error("gmail_send_failed", account_id=account.id, status=status_code, error=str(exc))
        raise GmailSendError(message, retryable=retryable, status_code=status_code) from exc
    except Exception as exc:
        text = str(exc)
        if "invalid_grant" in text or "expired or revoked" in text.lower():
            account.status = "expired"
            account.last_error = "Gmail access expired. Reconnect Gmail."
            db.add(account)
            db.commit()
            raise GmailSendError("Gmail access expired. Reconnect Gmail.", retryable=False) from exc
        account.last_error = "Unexpected Gmail send error"
        db.add(account)
        db.commit()
        logger.error("gmail_send_unexpected", account_id=account.id, error=str(exc))
        raise GmailSendError(str(exc)[:240], retryable=True) from exc
