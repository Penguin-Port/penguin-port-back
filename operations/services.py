import hashlib
from datetime import timedelta

from django.utils import timezone

from operations.models import (
    AuditLog,
    DemoMessage,
    Notification,
    OutboxEvent,
    PrivacyRetentionPolicy,
)
from integrations.providers import DeliveryResult, get_notification_provider


def emit_event(*, store, type: str, aggregate_type: str, aggregate_id, payload: dict):
    return OutboxEvent.objects.create(
        store=store,
        type=type,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        payload=payload,
    )


def write_audit(
    *,
    store,
    actor,
    action: str,
    resource_type: str,
    resource_id="",
    before=None,
    after=None,
    request_id="",
    ip_address="",
):
    ip_hash = hashlib.sha256(ip_address.encode()).hexdigest() if ip_address else ""
    return AuditLog.objects.create(
        store=store,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        before=before or {},
        after=after or {},
        request_id=request_id,
        ip_hash=ip_hash,
    )


def send_demo_notification(
    *,
    store,
    channel: str,
    template: str,
    destination_last4: str,
    payload: dict,
    destination: str = "",
):
    """Send through the configured provider and keep an audit/demo inbox row.

    The historical function name is retained for callers and tests. Set
    ``NOTIFICATION_PROVIDER=SOLAPI`` or ``HTTP`` to use a real gateway;
    ``DEMO`` remains deterministic for local development.
    """
    body = (
        f"인증번호: {payload['demoCode']}"
        if template == "OTP_CODE" and payload.get("demoCode")
        else str(payload.get("body") or template)
    )
    provider = get_notification_provider()
    if channel == Notification.Channel.SMS:
        if provider.name != "DEMO" and not destination:
            raise ValueError("실제 SMS 발송에는 destination 전화번호가 필요합니다.")
        delivery = provider.send_sms(
            destination=destination,
            body=body,
            payload=payload,
        )
    elif channel == Notification.Channel.ALIMTALK:
        if provider.name != "DEMO" and not destination:
            raise ValueError("실제 알림톡 발송에는 destination 전화번호가 필요합니다.")
        delivery = provider.send_alimtalk(
            destination=destination,
            body=body,
            payload=payload,
        )
    else:
        # In-app messages have no external destination and are represented by
        # the database notification log.
        delivery = DeliveryResult("IN_APP", "in-app")

    demo_message = DemoMessage.objects.create(
        store=store,
        channel=channel,
        destination_last4=destination_last4,
        body=body,
        payload=payload,
    )
    notification = Notification.objects.create(
        store=store,
        channel=channel,
        template=template,
        destination_last4=destination_last4,
        payload=payload,
        provider=delivery.provider,
        status=Notification.Status.SENT if delivery.status == "SENT" else Notification.Status.FAILED,
        attempts=1,
        provider_reference=delivery.reference,
        sent_at=timezone.now() if delivery.status == "SENT" else None,
    )
    # Notification은 운영 이력이고 DemoMessage는 PDF MVP의 Demo Inbox다.
    notification.payload = {**notification.payload, "demoMessageId": str(demo_message.id)}
    notification.save(update_fields=["payload"])
    return notification


def get_or_create_retention_policy(store):
    policy, _ = PrivacyRetentionPolicy.objects.get_or_create(store=store)
    return policy


def purge_expired_privacy_data(*, now=None):
    from identity.models import VerificationChallenge

    now = now or timezone.now()
    total = 0
    for policy in PrivacyRetentionPolicy.objects.select_related("store"):
        from orders.models import Order

        phone_cutoff = now - timedelta(days=policy.phone_retention_days)
        total += Order.objects.filter(
            store=policy.store,
            created_at__lt=phone_cutoff,
        ).exclude(phone_ciphertext="").update(phone_ciphertext="")
        verification_cutoff = now - timedelta(days=policy.verification_retention_days)
        deleted, _ = VerificationChallenge.objects.filter(
            store=policy.store,
            created_at__lt=verification_cutoff,
        ).delete()
        total += deleted
        audit_cutoff = now - timedelta(days=policy.audit_retention_days)
        deleted, _ = AuditLog.objects.filter(
            store=policy.store,
            created_at__lt=audit_cutoff,
        ).delete()
        total += deleted
    return total
