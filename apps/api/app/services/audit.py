from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models import AuditLog

logger = get_logger("audit")


def write_audit(
    db: Session,
    *,
    user_id: str | None,
    action: str,
    entity_type: str = "",
    entity_id: str = "",
    details: dict | None = None,
    ip_address: str = "",
    user_agent: str = "",
) -> None:
    entry = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details or {},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(entry)
    logger.info(
        "audit",
        action=action,
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
    )
