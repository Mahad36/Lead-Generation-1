import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger("crypto")
_warned = False


def _fernet() -> Fernet:
    global _warned
    settings = get_settings()
    key = (settings.encryption_key or "").strip()
    if key:
        try:
            return Fernet(key.encode())
        except ValueError:
            if settings.is_production:
                raise RuntimeError("ENCRYPTION_KEY is not a valid Fernet key") from None
    elif settings.is_production:
        raise RuntimeError("ENCRYPTION_KEY is not configured")
    # Development convenience: a stable key derived from SECRET_KEY, so a fresh checkout works
    # without generating a Fernet key first. Production always requires a real ENCRYPTION_KEY.
    if not _warned:
        logger.warning("encryption_key_derived_for_development")
        _warned = True
    digest = hashlib.sha256(f"leadgen-dev-encryption:{settings.secret_key}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_text(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_text(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Unable to decrypt stored credentials") from exc
