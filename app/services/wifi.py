from datetime import datetime, timedelta
from math import ceil

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import WiFiPass
from app.services.demo_network import revoke
from app.services.events import publish_event
from app.time import aware, db_now, normalize


TERMINAL_PASS_STATUSES = {"EXPIRED", "BLOCKED", "CANCELLED", "FAILED"}
PASS_REACTIVATION_BLOCKED_STATUSES = {"BLOCKED", "CANCELLED", "FAILED"}


def prorated_refunded_wifi_minutes(
    *, wifi_minutes: int, total_amount: int, refunded_amount: int
) -> int:
    """Return the Wi-Fi minutes attributable to the refunded amount."""

    if wifi_minutes <= 0 or total_amount <= 0 or refunded_amount <= 0:
        return 0
    return min(
        wifi_minutes,
        (wifi_minutes * refunded_amount + total_amount - 1) // total_amount,
    )


def reclaim_wifi_minutes(
    wifi_pass: WiFiPass | None, *, minutes: int, now: datetime | None = None
) -> int:
    """Remove unexpired Wi-Fi time and expire the pass when no time remains."""

    if wifi_pass is None or minutes <= 0 or wifi_pass.status in TERMINAL_PASS_STATUSES:
        return 0

    current_time = normalize(now) if now is not None else db_now()
    current_expiry = normalize(wifi_pass.expires_at)
    if current_expiry <= current_time:
        return 0

    next_expiry = max(current_time, current_expiry - timedelta(minutes=minutes))
    removed_minutes = ceil((current_expiry - next_expiry).total_seconds() / 60)
    if next_expiry == current_expiry:
        return 0

    wifi_pass.expires_at = next_expiry
    wifi_pass.version += 1
    if next_expiry <= current_time:
        revoke(wifi_pass.network_reference or "")
        wifi_pass.status = "EXPIRED"
        wifi_pass.network_reference = None
    return removed_minutes


def pass_data(wifi_pass: WiFiPass, *, now: datetime | None = None) -> dict:
    current_time = normalize(now) if now is not None else db_now()
    if wifi_pass.status in TERMINAL_PASS_STATUSES:
        remaining_seconds = 0
    else:
        remaining_seconds = max(
            0,
            ceil((normalize(wifi_pass.expires_at) - current_time).total_seconds()),
        )
    return {
        "passId": wifi_pass.id,
        "status": wifi_pass.status,
        "issuedAt": aware(wifi_pass.issued_at).isoformat(),
        "activatedAt": aware(wifi_pass.activated_at).isoformat() if wifi_pass.activated_at else None,
        "expiresAt": aware(wifi_pass.expires_at).isoformat(),
        "remainingSeconds": remaining_seconds,
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
        publish_event(
            db,
            store_id=wifi_pass.store_id,
            event_type="wifi.pass.expired",
            aggregate_type="WiFiPass",
            aggregate_id=wifi_pass.id,
            payload={
                "passId": wifi_pass.id,
                "status": wifi_pass.status,
                "version": wifi_pass.version,
                "source": "expiry_loop",
            },
        )
        count += 1
    if count:
        db.commit()
    return count
