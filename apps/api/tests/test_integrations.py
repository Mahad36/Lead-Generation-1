from unittest.mock import MagicMock

from app.schemas.auth import LoginRequest
from app.services.ai_catalog import normalize_model
from app.services.integrations import resolve_ai_config, resolve_google_oauth


def test_login_accepts_local_dev_email():
    payload = LoginRequest(email="demo@leadgen.local", password="DemoPass123!")
    assert payload.email == "demo@leadgen.local"


def test_user_ai_key_overrides_env(monkeypatch):
    from app.services import integrations

    monkeypatch.setattr(integrations, "_decrypt_or_empty", lambda value: value)
    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: MagicMock(
            ai_api_key="env-key",
            ai_api_base="https://env.example/v1",
            ai_provider="openai",
            ai_model="gpt-4o-mini",
            ai_temperature=0.4,
            ai_timeout_seconds=30,
        ),
    )
    row = MagicMock(
        encrypted_ai_api_key="user-key",
        ai_api_base="https://user.example/v1",
        ai_provider="groq",
        ai_model="llama-3.1-8b",
        ai_temperature=0.1,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = row
    cfg = resolve_ai_config(db, "user-1")
    assert cfg.api_key == "user-key"
    assert cfg.api_base == "https://user.example/v1"
    assert cfg.provider == "groq"
    assert cfg.model == "llama-3.1-8b"


def test_gemini_uses_catalog_defaults_when_base_empty(monkeypatch):
    from app.services import integrations

    monkeypatch.setattr(integrations, "_decrypt_or_empty", lambda value: value)
    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: MagicMock(
            ai_api_key="",
            ai_api_base="https://api.openai.com/v1",
            ai_provider="openai",
            ai_model="gpt-4o-mini",
            ai_temperature=0.4,
            ai_timeout_seconds=30,
        ),
    )
    row = MagicMock(
        encrypted_ai_api_key="user-key",
        ai_api_base="",
        ai_provider="gemini",
        ai_model="",
        ai_temperature=0.2,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = row
    cfg = resolve_ai_config(db, "user-1")
    assert cfg.provider == "gemini"
    assert "generativelanguage.googleapis.com" in cfg.api_base
    assert cfg.model == "gemini-3.6-flash"


def test_retired_gemini_model_is_remapped():
    assert normalize_model("gemini-2.5-flash") == "gemini-3.6-flash"


def test_user_google_oauth_overrides_env(monkeypatch):
    from app.services import integrations

    monkeypatch.setattr(integrations, "_decrypt_or_empty", lambda value: value)
    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: MagicMock(
            google_client_id="env-id",
            google_client_secret="env-secret",
            google_redirect_uri="http://localhost:8003/api/email-accounts/gmail/callback",
        ),
    )
    row = MagicMock(google_client_id="user-id", encrypted_google_client_secret="user-secret")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = row
    cfg = resolve_google_oauth(db, "user-1")
    assert cfg.client_id == "user-id"
    assert cfg.client_secret == "user-secret"
    assert "gmail/callback" in cfg.redirect_uri


def test_configured_redirect_uri_wins_over_request_url(monkeypatch):
    from app.config import Settings
    from app.services import gmail

    public = "https://demo-3000.app.github.dev/api/email-accounts/gmail/callback"
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", public)
    monkeypatch.setattr("app.config.get_settings", lambda: Settings())
    request = MagicMock(base_url="http://localhost:8000/")
    assert gmail.oauth_redirect_uri(request) == public
    assert gmail.suggested_redirect_uris() == [public]


def test_request_url_used_when_redirect_uri_not_configured(monkeypatch):
    from app.config import Settings
    from app.services import gmail

    monkeypatch.delenv("GOOGLE_REDIRECT_URI", raising=False)
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(_env_file=None))
    request = MagicMock(base_url="http://localhost:8000/")
    assert gmail.oauth_redirect_uri(request) == "http://localhost:8000/api/email-accounts/gmail/callback"
