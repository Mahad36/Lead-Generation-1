from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import HealthAlert, User
from app.services.audit import write_audit
from app.services.health_alerts import as_dict, check_user

router = APIRouter(prefix="/health-alerts", tags=["health-alerts"])

RANK = {"critical": 0, "warning": 1}


def _open(db: Session, user_id: str) -> list[HealthAlert]:
    rows = db.query(HealthAlert).filter(HealthAlert.user_id == user_id, HealthAlert.resolved_at.is_(None)).all()
    return sorted(rows, key=lambda a: (RANK.get(a.severity, 2), -(a.created_at.timestamp() if a.created_at else 0)))


@router.get("")
def list_alerts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Open alerts (most serious first) and the ones resolved in the last 30 days."""
    since = datetime.now(UTC) - timedelta(days=30)
    resolved = (
        db.query(HealthAlert)
        .filter(HealthAlert.user_id == user.id, HealthAlert.resolved_at >= since)
        .order_by(HealthAlert.resolved_at.desc())
        .limit(50)
        .all()
    )
    return {"open": [as_dict(a) for a in _open(db, user.id)], "resolved": [as_dict(a) for a in resolved]}


@router.get("/summary")
def summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """For the bell: counts, plus alerts not shown as a pop-up yet."""
    alerts = _open(db, user.id)
    return {
        "open": len(alerts),
        "critical": sum(1 for a in alerts if a.severity == "critical"),
        "unread": sum(1 for a in alerts if not a.read_at),
        "to_notify": [as_dict(a) for a in alerts if not a.notified_at],
        "top_critical": next((as_dict(a) for a in alerts if a.severity == "critical"), None),
    }


class IdsIn(BaseModel):
    ids: list[str] = []


@router.post("/notified")
def mark_notified(data: IdsIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    now = datetime.now(UTC)
    db.query(HealthAlert).filter(
        HealthAlert.user_id == user.id, HealthAlert.id.in_(data.ids), HealthAlert.notified_at.is_(None)
    ).update({"notified_at": now}, synchronize_session=False)
    db.commit()
    return {"ok": True}


@router.post("/read")
def mark_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Opening the Email health page: every open alert counts as seen."""
    now = datetime.now(UTC)
    db.query(HealthAlert).filter(HealthAlert.user_id == user.id, HealthAlert.read_at.is_(None)).update(
        {"read_at": now, "notified_at": func.coalesce(HealthAlert.notified_at, now)}, synchronize_session=False
    )
    db.commit()
    return {"ok": True}


@router.post("/{alert_id}/acknowledge")
def acknowledge(alert_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """You reviewed a warning: it stops holding sending (the inbox still sends at reduced capacity while it lasts).
    Critical alerts can't be acknowledged; they hold sending until the cause clears. Comes back if it gets worse."""
    alert = db.query(HealthAlert).filter(HealthAlert.id == alert_id, HealthAlert.user_id == user.id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.severity == "critical":
        raise HTTPException(
            status_code=400,
            detail="Critical warnings can't be overridden: sending stays held until the cause clears.",
        )
    alert.acknowledged_at = datetime.now(UTC)
    write_audit(db, user_id=user.id, action="health_alert.acknowledged", entity_type="health_alert",
                entity_id=alert.id, details={"key": alert.key, "title": alert.title})
    db.commit()
    return as_dict(alert)


@router.post("/{alert_id}/unacknowledge")
def unacknowledge(alert_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    alert = db.query(HealthAlert).filter(HealthAlert.id == alert_id, HealthAlert.user_id == user.id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.acknowledged_at = None
    db.commit()
    return as_dict(alert)


@router.post("/check")
def check_now(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Check everything again now, including the daily domain checks (DNS records, blacklists)."""
    from app.models import EmailAccount
    from app.services.account_health import assess

    for account in db.query(EmailAccount).filter(EmailAccount.user_id == user.id, EmailAccount.status != "disconnected"):
        try:
            assess(db, account)
        except Exception:
            db.rollback()
    result = check_user(db, user.id, force=True)
    return {**result, **list_alerts(user, db)}
