from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import SuppressionEntry
from app.utils.normalize import normalize_email


def is_suppressed(db: Session, user_id: str, email: str) -> SuppressionEntry | None:
    value = normalize_email(email)
    if not value:
        return None
    return (
        db.query(SuppressionEntry)
        .filter(SuppressionEntry.user_id == user_id, SuppressionEntry.email == value)
        .first()
    )


def add_suppression(
    db: Session,
    user_id: str,
    email: str,
    reason: str,
    source: str = "manual",
    notes: str = "",
) -> SuppressionEntry:
    value = normalize_email(email)
    if not value:
        raise HTTPException(status_code=400, detail="Email is required")
    existing = is_suppressed(db, user_id, value)
    if existing:
        existing.reason = reason
        existing.source = source
        existing.notes = notes
        db.commit()
        db.refresh(existing)
        return existing
    entry = SuppressionEntry(
        user_id=user_id, email=value, reason=reason, source=source, notes=notes
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def list_suppressions(db: Session, user_id: str) -> list[SuppressionEntry]:
    return (
        db.query(SuppressionEntry)
        .filter(SuppressionEntry.user_id == user_id)
        .order_by(SuppressionEntry.created_at.desc())
        .all()
    )


def delete_suppression(db: Session, user_id: str, entry_id: str) -> None:
    entry = (
        db.query(SuppressionEntry)
        .filter(SuppressionEntry.id == entry_id, SuppressionEntry.user_id == user_id)
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    db.delete(entry)
    db.commit()
