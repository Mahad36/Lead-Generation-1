from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_client_meta, get_current_user
from app.models import EmailAccount, EmailMessage, User
from app.services.audit import write_audit
from pydantic import BaseModel, EmailStr, Field

from app.services.gmail import (
    GmailSendError,
    build_authorization_url,
    exchange_code,
    oauth_redirect_uri,
    persist_refreshed_credentials,
    require_google_oauth,
    send_gmail_message,
    sent_today_count,
    store_account,
    store_smtp_account,
    verify_smtp_login,
    _credentials_from_account,
    _smtp_payload,
)
from app.utils.security import create_oauth_state_token, try_decode_token

router = APIRouter(prefix="/email-accounts", tags=["email-accounts"])


class SmtpConnectIn(BaseModel):
    email_address: EmailStr
    host: str = Field(min_length=1, max_length=255)
    port: int = 587
    username: str = ""
    password: str = Field(min_length=1)
    use_tls: bool = True
    display_name: str = ""


@router.get("")
def list_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    accounts = (
        db.query(EmailAccount)
        .filter(EmailAccount.user_id == user.id)
        .order_by(EmailAccount.created_at.desc())
        .all()
    )
    from app.services.deliverability import effective_daily_limit
    from app.services.gmail_signature import signature_info
    from app.services.warmup import warmup_stats

    out = []
    for account in accounts:
        sent_today = sent_today_count(db, account)
        limit_today = effective_daily_limit(account)
        status = account.status
        if status == "connected" and "expired" in (account.last_error or "").lower():
            status = "expired"
        out.append(
            {
                "id": account.id,
                "email_address": account.email_address,
                "display_name": account.display_name,
                "status": status,
                "daily_limit": account.daily_limit,
                # A new inbox ramps up: today's limit is lower than daily_limit until it catches up
                "limit_today": limit_today,
                "ramping_up": limit_today < (account.daily_limit or 0),
                "sent_today": sent_today,
                "remaining_today": 0 if account.paused_reason else max(limit_today - sent_today, 0),
                "paused_reason": account.paused_reason,
                "paused_at": account.paused_at,
                "warmup_enabled": account.warmup_enabled,
                "warmup": warmup_stats(db, account) if _smtp_payload(account) else None,
                "health": account.health or {},
                "gmail_signature": signature_info(account),
                "last_successful_send_at": account.last_successful_send_at,
                "last_error": account.last_error,
                "provider": account.provider,
                "is_default": account.is_default,
                "last_used_at": account.last_used_at,
                "inbox_checked_at": account.inbox_checked_at,
                "inbox_error": account.inbox_error,
            }
        )
    return out


@router.post("/smtp")
def connect_smtp(payload: SmtpConnectIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        account = store_smtp_account(
            db,
            user.id,
            email_address=str(payload.email_address),
            host=payload.host,
            port=payload.port,
            username=payload.username,
            password=payload.password,
            use_tls=payload.use_tls,
            display_name=payload.display_name,
        )
    except GmailSendError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from app.services.gmail_signature import refresh_in_background

    refresh_in_background(account.id)  # read this Gmail's own signature from its Sent folder
    return {"id": account.id, "email_address": account.email_address, "status": account.status, "provider": "smtp"}


@router.get("/gmail/connect")
def connect_gmail(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_google_oauth(db, user.id)
    state = create_oauth_state_token(user.id)
    redirect_uri = oauth_redirect_uri(request)
    return {"authorization_url": build_authorization_url(db, user.id, state, redirect_uri), "redirect_uri": redirect_uri}


@router.get("/gmail/callback")
def gmail_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    settings = get_settings()
    if error:
        return RedirectResponse(f"{settings.frontend_url}/email-accounts?error={error}")
    payload = try_decode_token(state)
    if not payload or payload.get("sub") is None or payload.get("type") != "oauth_state":
        return RedirectResponse(f"{settings.frontend_url}/email-accounts?error=invalid_state")
    token_payload = exchange_code(db, payload["sub"], code, oauth_redirect_uri(request))
    account = store_account(db, payload["sub"], token_payload)
    write_audit(db, user_id=payload["sub"], action="gmail.connected", entity_type="email_account", entity_id=account.id)
    db.commit()
    return RedirectResponse(f"{settings.frontend_url}/email-accounts?connected={account.id}")


@router.post("/{account_id}/disconnect")
def disconnect(
    account_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = (
        db.query(EmailAccount)
        .filter(EmailAccount.id == account_id, EmailAccount.user_id == user.id)
        .first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    account.status = "disconnected"
    account.encrypted_credentials = ""
    write_audit(
        db,
        user_id=user.id,
        action="gmail.disconnected",
        entity_type="email_account",
        entity_id=account.id,
        **get_client_meta(request),
    )
    db.commit()
    return {"message": "Disconnected"}


@router.post("/{account_id}/refresh")
def refresh_account(
    account_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = (
        db.query(EmailAccount)
        .filter(EmailAccount.id == account_id, EmailAccount.user_id == user.id)
        .first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.status == "disconnected" or not account.encrypted_credentials:
        raise HTTPException(status_code=400, detail="Account is disconnected. Reconnect Gmail or SMTP.")
    smtp = _smtp_payload(account)
    if smtp:
        try:
            verify_smtp_login(
                smtp.get("host") or "",
                int(smtp.get("port") or 587),
                smtp.get("username") or account.email_address,
                smtp.get("password") or "",
                bool(smtp.get("use_tls", True)),
            )
        except GmailSendError as exc:
            account.status = "error"
            account.last_error = str(exc)[:240]
            db.commit()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        account.status = "connected"
        account.last_error = ""
        db.commit()
        return {"message": "SMTP connection verified", "status": account.status}
    creds = _credentials_from_account(account)
    persist_refreshed_credentials(db, account, creds)
    db.commit()
    return {"message": "Credentials refreshed", "status": account.status}


@router.post("/{account_id}/default")
def set_default_account(
    account_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = (
        db.query(EmailAccount)
        .filter(EmailAccount.id == account_id, EmailAccount.user_id == user.id)
        .first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    db.query(EmailAccount).filter(EmailAccount.user_id == user.id).update({"is_default": False})
    account.is_default = True
    db.commit()
    return {"message": "Default sending account updated"}


def _own_account(db: Session, user: User, account_id: str) -> EmailAccount:
    account = db.query(EmailAccount).filter(EmailAccount.id == account_id, EmailAccount.user_id == user.id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.post("/{account_id}/resume")
def resume_account(account_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Resume an inbox that paused itself after too many bounces (and the campaigns waiting for it)."""
    from app.models import Campaign

    account = _own_account(db, user, account_id)
    account.paused_reason, account.paused_at = "", None
    waiting = (
        db.query(Campaign)
        .filter(Campaign.user_id == user.id, Campaign.status == "paused", Campaign.paused_reason == "inbox_paused_bounces")
        .all()
    )
    for campaign in waiting:
        campaign.status, campaign.paused_reason = "running", ""
    db.commit()
    if waiting:
        from app.services.scraping_jobs import enqueue_task
        from workers.tasks.campaigns import process_campaign_queue

        for campaign in waiting:
            enqueue_task(process_campaign_queue, campaign.id)
    from app.services.auto_audits import request_poll

    request_poll()  # emails waiting for this inbox go out
    return {"message": f"{account.email_address} is sending again"}


class WarmupIn(BaseModel):
    enabled: bool


@router.post("/{account_id}/warmup")
def set_warmup(account_id: str, payload: WarmupIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = _own_account(db, user, account_id)
    account.warmup_enabled = payload.enabled
    db.commit()
    return {"warmup_enabled": account.warmup_enabled}


@router.post("/{account_id}/signature/refresh")
def refresh_gmail_signature(account_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Read this Gmail's current signature again (from the latest email written in Gmail)."""
    from app.services.gmail_signature import refresh_signature

    return refresh_signature(db, _own_account(db, user, account_id))


@router.post("/{account_id}/domain-check")
def check_domain(account_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """SPF / DKIM / DMARC for the inbox's domain."""
    from app.services.warmup import domain_check

    return domain_check(db, _own_account(db, user, account_id))


@router.post("/{account_id}/placement-test")
def placement_test(account_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Send your latest outreach email to your other inboxes and report Inbox / Promotions / Spam (takes ~2 min)."""
    from app.services.warmup import start_placement_test

    account = _own_account(db, user, account_id)
    if account.status != "connected":
        raise HTTPException(status_code=400, detail="Reconnect this inbox first.")
    return start_placement_test(db, account)


@router.post("/{account_id}/test")
def test_email(
    account_id: str,
    request: Request,
    to: str = Query(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = (
        db.query(EmailAccount)
        .filter(EmailAccount.id == account_id, EmailAccount.user_id == user.id)
        .first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    recipient = to or user.email
    html = "<p>This is a test email from Lead Generator.</p>"
    try:
        provider_id = send_gmail_message(
            db, account, to=recipient, subject="Lead Generator test email", html_body=html, text_body="Test email"
        )
    except GmailSendError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.add(
        EmailMessage(
            user_id=user.id,
            email_account_id=account.id,
            recipient_email=recipient,
            recipient_name=user.full_name,
            subject="Lead Generator test email",
            html_body=html,
            text_body="Test email",
            status="sent",
            provider_message_id=provider_id,
            sent_at=datetime.now(UTC),
            is_test=True,
        )
    )
    write_audit(
        db,
        user_id=user.id,
        action="email.test_sent",
        entity_type="email_account",
        entity_id=account.id,
        **get_client_meta(request),
    )
    db.commit()
    return {"message": "Test email sent", "provider_message_id": provider_id}
