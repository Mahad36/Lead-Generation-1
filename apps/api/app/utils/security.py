import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings

try:
    import bcrypt as _bcrypt

    if not hasattr(_bcrypt, "__about__"):
        class _About:
            __version__ = getattr(_bcrypt, "__version__", "4.0.0")

        _bcrypt.__about__ = _About()  # type: ignore[attr-defined]
except Exception:
    pass

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str, extra: dict | None = None) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": subject, "exp": expire, "type": "access"}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def create_oauth_state_token(user_id: str) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=15)
    payload = {
        "sub": user_id,
        "exp": expire,
        "type": "oauth_state",
        "nonce": secrets.token_hex(16),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def create_unsubscribe_token(user_id: str, email: str) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(days=365)
    payload = {"sub": user_id, "email": email, "exp": expire, "type": "unsubscribe"}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def create_refresh_token_value() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def decode_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])


def try_decode_token(token: str) -> dict | None:
    try:
        return decode_token(token)
    except JWTError:
        return None
