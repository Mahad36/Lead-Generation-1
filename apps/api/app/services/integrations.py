from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import UserSettings
from app.services.ai_catalog import apply_provider_defaults, normalize_model
from app.utils.crypto import decrypt_text


@dataclass(frozen=True)
class AIConfig:
    api_key: str
    api_base: str
    provider: str
    model: str
    temperature: float
    timeout_seconds: int


@dataclass(frozen=True)
class GoogleOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str


def _decrypt_or_empty(value: str) -> str:
    if not value:
        return ""
    try:
        return decrypt_text(value)
    except Exception:
        return ""


def load_user_settings(db: Session, user_id: str) -> UserSettings | None:
    return db.query(UserSettings).filter(UserSettings.user_id == user_id).first()


def resolve_ai_config(db: Session, user_id: str) -> AIConfig:
    app = get_settings()
    settings = load_user_settings(db, user_id)
    user_key = _decrypt_or_empty(settings.encrypted_ai_api_key) if settings else ""
    provider, catalog_base, catalog_model = apply_provider_defaults(
        (settings.ai_provider if settings and settings.ai_provider else "") or app.ai_provider,
        "",
        "",
    )
    user_base = settings.ai_api_base.strip() if settings and settings.ai_api_base else ""
    user_model = normalize_model(settings.ai_model) if settings and settings.ai_model else ""
    return AIConfig(
        api_key=user_key or app.ai_api_key,
        api_base=user_base or catalog_base or app.ai_api_base,
        provider=provider,
        model=user_model or catalog_model or app.ai_model,
        temperature=settings.ai_temperature if settings is not None else app.ai_temperature,
        timeout_seconds=app.ai_timeout_seconds,
    )


def resolve_google_oauth(db: Session, user_id: str) -> GoogleOAuthConfig:
    app = get_settings()
    settings = load_user_settings(db, user_id)
    user_secret = _decrypt_or_empty(settings.encrypted_google_client_secret) if settings else ""
    return GoogleOAuthConfig(
        client_id=(settings.google_client_id if settings and settings.google_client_id else "") or app.google_client_id,
        client_secret=user_secret or app.google_client_secret,
        redirect_uri=app.google_redirect_uri,
    )


def resolve_apify_token(db: Session, user_id: str) -> str:
    """The user's Apify token from settings, else APIFY_TOKEN from the environment."""
    settings = load_user_settings(db, user_id)
    user_token = _decrypt_or_empty(settings.encrypted_apify_token) if settings else ""
    return (user_token or get_settings().apify_token or "").strip()


def resolve_scraper_provider(db: Session, user_id: str) -> str:
    """Google Maps lead source for this user: "scraper" (built-in) or "apify"."""
    settings = load_user_settings(db, user_id)
    choice = (settings.scraper_provider if settings else "") or get_settings().scraper_provider
    if choice == "auto":
        return "apify" if resolve_apify_token(db, user_id) else "scraper"
    return "apify" if choice == "apify" else "scraper"


def ai_is_configured(db: Session, user_id: str) -> bool:
    config = resolve_ai_config(db, user_id)
    if config.provider == "claude_code":
        import shutil

        from pathlib import Path

        return bool(shutil.which(get_settings().claude_code_bin) or shutil.which(str(Path.home() / ".local/bin/claude")))
    return bool(config.api_key)


def google_oauth_is_configured(db: Session, user_id: str) -> bool:
    cfg = resolve_google_oauth(db, user_id)
    return bool(cfg.client_id and cfg.client_secret)
