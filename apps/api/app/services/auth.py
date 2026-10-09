from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_config import get_logger
from app.models import PasswordResetToken, RefreshToken, User, UserSettings
from app.services.audit import write_audit
from app.utils.security import (
    create_access_token,
    create_refresh_token_value,
    hash_password,
    hash_token,
    verify_password,
)

logger = get_logger("auth")


def _issue_tokens(db: Session, user: User) -> dict:
    settings = get_settings()
    access = create_access_token(user.id)
    refresh = create_refresh_token_value()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(refresh),
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
        )
    )
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer"}


def register_user(db: Session, email: str, password: str, full_name: str, meta: dict) -> tuple[User, dict]:
    existing = db.query(User).filter(User.email == email.lower()).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    user = User(
        email=email.lower(),
        hashed_password=hash_password(password),
        full_name=full_name,
        is_verified=True,
    )
    db.add(user)
    db.flush()
    db.add(UserSettings(user_id=user.id, contact_email=email.lower(), sender_name=full_name))
    write_audit(db, user_id=user.id, action="user.registered", entity_type="user", entity_id=user.id, **meta)
    tokens = _issue_tokens(db, user)
    db.commit()
    try:
        from app.services.templates import ensure_default_template

        ensure_default_template(db, user.id)
    except Exception as exc:
        logger.warning("default_template_skipped", user_id=user.id, error=str(exc))
    db.refresh(user)
    logger.info("user_registered", user_id=user.id)
    return user, tokens


def login_user(db: Session, email: str, password: str, meta: dict) -> tuple[User, dict]:
    user = db.query(User).filter(User.email == email.lower()).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")
    user.last_login_at = datetime.now(UTC)
    write_audit(db, user_id=user.id, action="user.login", entity_type="user", entity_id=user.id, **meta)
    tokens = _issue_tokens(db, user)
    db.commit()
    return user, tokens


def refresh_tokens(db: Session, refresh_token: str) -> dict:
    token_row = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == hash_token(refresh_token), RefreshToken.revoked.is_(False))
        .first()
    )
    if not token_row or token_row.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    user = db.get(User, token_row.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    token_row.revoked = True
    tokens = _issue_tokens(db, user)
    db.commit()
    return tokens


def revoke_refresh_token(db: Session, refresh_token: str) -> None:
    token_row = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_token(refresh_token)).first()
    if token_row:
        token_row.revoked = True
        db.commit()


def create_password_reset(db: Session, email: str) -> str | None:
    user = db.query(User).filter(User.email == email.lower()).first()
    if not user:
        return None
    raw = create_refresh_token_value()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        )
    )
    write_audit(db, user_id=user.id, action="user.password_reset_requested", entity_type="user", entity_id=user.id)
    db.commit()
    logger.info("password_reset_created", user_id=user.id)
    return raw


def reset_password(db: Session, token: str, password: str) -> None:
    row = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.token_hash == hash_token(token),
            PasswordResetToken.used.is_(False),
        )
        .first()
    )
    if not row or row.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")
    user = db.get(User, row.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid reset token")
    user.hashed_password = hash_password(password)
    row.used = True
    write_audit(db, user_id=user.id, action="user.password_reset", entity_type="user", entity_id=user.id)
    db.commit()
