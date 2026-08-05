from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def record_audit(
    db: Session,
    *,
    store_id: str,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    actor_type: str = "SYSTEM",
    actor_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        store_id=store_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_json=metadata or {},
    )
    db.add(entry)
    return entry
