from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AuditLog, DemoMessage, Order, OtpChallenge
from app.time import db_now


def purge_sensitive_data(db: Session, *, now=None) -> int:
    now = now or db_now()
    phone_cutoff = now - timedelta(days=settings.phone_retention_days)
    audit_cutoff = now - timedelta(days=settings.audit_retention_days)
    changed = 0
    for order in db.scalars(
        select(Order).where(Order.created_at < phone_cutoff)
    ).all():
        if order.phone or order.phone_lookup_hash or order.phone_last4:
            order.phone = None
            order.phone_lookup_hash = None
            order.phone_last4 = None
            changed += 1
    old_challenges = db.scalars(
        select(OtpChallenge).where(OtpChallenge.created_at < phone_cutoff)
    ).all()
    if old_challenges:
        changed += len(old_challenges)
        for challenge in old_challenges:
            db.delete(challenge)
    result = db.execute(
        delete(DemoMessage).where(DemoMessage.created_at < phone_cutoff)
    )
    changed += result.rowcount or 0
    audit_result = db.execute(delete(AuditLog).where(AuditLog.created_at < audit_cutoff))
    changed += audit_result.rowcount or 0
    return changed
