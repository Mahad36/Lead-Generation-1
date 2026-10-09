from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_client_meta, get_current_user
from app.models import User, UserService, UserSettings
from app.schemas.settings import SettingsOut, SettingsUpdate
from app.services.ai import AINotConfiguredError, get_ai_provider_for_user
from app.services.ai_catalog import PROVIDERS, normalize_provider
from app.services.audit import write_audit
from app.services.gmail import suggested_redirect_uris
from app.services.integrations import (
    ai_is_configured,
    google_oauth_is_configured,
    resolve_apify_token,
    resolve_scraper_provider,
)
from app.services.limits import LimitValidationError, validate_send_limits
from app.utils.crypto import encrypt_text

router = APIRouter(prefix="/settings", tags=["settings"])

_SKIP_COLUMNS = {
    "id",
    "user_id",
    "created_at",
    "updated_at",
    "encrypted_ai_api_key",
    "encrypted_google_client_secret",
    "encrypted_apify_token",
}
_SECRET_FIELDS = {"ai_api_key", "google_client_secret", "apify_token"}


def _ai_error_message(exc: Exception) -> str:
    text = str(exc)
    if "no longer available" in text.lower() or "NOT_FOUND" in text:
        return "That Gemini model is retired. The app now uses gemini-3.6-flash — save settings and test again."
    if "AI request failed:" in text:
        return text
    return f"AI request failed: {text[:280]}"


def _payload(db: Session, user: User) -> dict:
    settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    if not settings:
        settings = UserSettings(user_id=user.id)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    app = get_settings()
    data = {c.name: getattr(settings, c.name) for c in settings.__table__.columns if c.name not in _SKIP_COLUMNS}
    data["ai_api_key_set"] = bool(settings.encrypted_ai_api_key) or bool(app.ai_api_key)
    data["google_client_secret_set"] = bool(settings.encrypted_google_client_secret) or bool(app.google_client_secret)
    data["ai_configured"] = ai_is_configured(db, user.id)
    data["apify_token_set"] = bool(settings.encrypted_apify_token) or bool(app.apify_token)
    data["apify_configured"] = bool(resolve_apify_token(db, user.id))
    data["scraper_provider"] = resolve_scraper_provider(db, user.id)
    data["google_oauth_configured"] = google_oauth_is_configured(db, user.id)
    data["google_redirect_uri"] = app.google_redirect_uri
    data["google_redirect_uris"] = suggested_redirect_uris()
    data["services"] = settings.user.services if settings.user else user.services
    return data


@router.get("/ai-providers")
def list_ai_providers(user: User = Depends(get_current_user)):
    return PROVIDERS


@router.get("", response_model=SettingsOut)
def get_settings_endpoint(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _payload(db, user)


@router.put("", response_model=SettingsOut)
def update_settings(
    payload: SettingsUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    if not settings:
        settings = UserSettings(user_id=user.id)
        db.add(settings)
        db.flush()
    data = payload.model_dump(exclude_unset=True)
    services = data.pop("services", None)
    ai_api_key = data.pop("ai_api_key", None)
    google_client_secret = data.pop("google_client_secret", None)
    apify_token = data.pop("apify_token", None)
    if data.get("scraper_provider") == "apify" and not (
        (isinstance(apify_token, str) and apify_token.strip()) or resolve_apify_token(db, user.id)
    ):
        raise HTTPException(status_code=400, detail="Add your Apify API token to use Apify, or choose the built-in scraper.")
    if any(
        k in data
        for k in (
            "default_daily_send_limit",
            "default_hourly_send_limit",
            "default_min_send_delay_seconds",
            "default_max_send_delay_seconds",
        )
    ):
        try:
            daily = data.get("default_daily_send_limit", settings.default_daily_send_limit)
            daily, hourly, min_d, max_d = validate_send_limits(
                daily,
                daily,
                data.get("default_min_send_delay_seconds", settings.default_min_send_delay_seconds),
                data.get("default_max_send_delay_seconds", settings.default_max_send_delay_seconds),
            )
        except LimitValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        data["default_daily_send_limit"] = daily
        data["default_hourly_send_limit"] = hourly
        data["default_min_send_delay_seconds"] = min_d
        data["default_max_send_delay_seconds"] = max_d
        from app.services.campaigns import apply_daily_limit

        apply_daily_limit(db, user.id, daily)
        write_audit(
            db,
            user_id=user.id,
            action="settings.send_limits_changed",
            entity_type="user_settings",
            entity_id=settings.id,
            details={"daily": daily, "hourly": hourly},
            **get_client_meta(request),
        )
    if data.get("ai_provider"):
        data["ai_provider"] = normalize_provider(str(data["ai_provider"]))
    for key, value in data.items():
        if key in _SECRET_FIELDS:
            continue
        setattr(settings, key, value)
    if isinstance(ai_api_key, str) and ai_api_key.strip():
        settings.encrypted_ai_api_key = encrypt_text(ai_api_key.strip())
    if isinstance(google_client_secret, str) and google_client_secret.strip():
        settings.encrypted_google_client_secret = encrypt_text(google_client_secret.strip())
    if isinstance(apify_token, str) and apify_token.strip():
        settings.encrypted_apify_token = encrypt_text(apify_token.strip())
    if services is not None:
        db.query(UserService).filter(UserService.user_id == user.id).delete()
        for index, item in enumerate(services):
            db.add(
                UserService(
                    user_id=user.id,
                    name=item["name"],
                    description=item.get("description") or "",
                    is_active=item.get("is_active", True),
                    sort_order=item.get("sort_order", index),
                )
            )
    write_audit(
        db,
        user_id=user.id,
        action="settings.updated",
        entity_type="user_settings",
        entity_id=settings.id,
        **get_client_meta(request),
    )
    db.commit()
    db.refresh(settings)
    return _payload(db, user)


@router.get("/portfolio")
def portfolio_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """How many reference sites the portfolio sheet has, and how many still load."""
    from app.services import portfolio

    return portfolio.status(db, user.id)


@router.post("/portfolio/reload")
def portfolio_reload(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services import portfolio

    return portfolio.status(db, user.id, force=True)


@router.post("/test-ai")
def test_ai(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        provider = get_ai_provider_for_user(db, user.id)
        sample = provider.generate("Reply with the single word ok.", system="Reply with one word only.")
    except AINotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=_ai_error_message(exc)) from exc
    return {"ok": True, "sample": sample[:240]}
