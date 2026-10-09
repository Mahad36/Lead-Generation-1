from types import SimpleNamespace

import pytest

from app.utils import crypto


def _settings(key: str, production: bool = False):
    return SimpleNamespace(encryption_key=key, secret_key="s3cret", is_production=production)


@pytest.mark.parametrize("key", ["", "generate-a-fernet-key"])
def test_development_derives_stable_key_when_missing_or_placeholder(monkeypatch, key):
    monkeypatch.setattr(crypto, "get_settings", lambda: _settings(key))
    token = crypto.encrypt_text("hello")
    assert crypto.decrypt_text(token) == "hello"


@pytest.mark.parametrize("key", ["", "generate-a-fernet-key"])
def test_production_requires_real_key(monkeypatch, key):
    monkeypatch.setattr(crypto, "get_settings", lambda: _settings(key, production=True))
    with pytest.raises(RuntimeError):
        crypto.encrypt_text("hello")


def test_real_key_is_used(monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(crypto, "get_settings", lambda: _settings(key))
    token = crypto.encrypt_text("hello")
    assert Fernet(key.encode()).decrypt(token.encode()) == b"hello"
