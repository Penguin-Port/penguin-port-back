from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import WiFiPass
from app.services.demo_network import revoke
from app.time import aware, db_now, normalize


def pass_data(wifi_pass: WiFiPass) -> dict:
    return {
        "passId": wifi_pass.id,
        "status": wifi_pass.status,
        "issuedAt": aware(wifi_pass.issued_at).isoformat(),
        "activatedAt": aware(wifi_pass.activated_at).isoformat() if wifi_pass.activated_at else None,
        "expiresAt": aware(wifi_pass.expires_at).isoformat(),
        "version": wifi_pass.version,
        "policySnapshot": wifi_pass.policy_snapshot,
    }


def expire_due_passes(db: Session, *, now: datetime | None = None) -> int:
    now = normalize(now) if now else db_now()
    passes = db.scalars(
        select(WiFiPass).where(
            WiFiPass.status.in_(["ACTIVE", "EXPIRING_SOON"]),
            WiFiPass.expires_at <= now,
        )
    ).all()
    count = 0
    for wifi_pass in passes:
        revoke(wifi_pass.network_reference or "")
        wifi_pass.status = "EXPIRED"
        wifi_pass.network_reference = None
        wifi_pass.version += 1
        count += 1
    if count:
        db.commit()
    return count
