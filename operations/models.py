import uuid

from django.conf import settings
from django.db import models

from stores.models import Store


class Notification(models.Model):
    class Channel(models.TextChoices):
        SMS = "SMS", "SMS"
        ALIMTALK = "ALIMTALK", "Alimtalk"
        IN_APP = "IN_APP", "In-app"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="notifications")
    channel = models.CharField(max_length=20, choices=Channel.choices)
    template = models.CharField(max_length=80)
    destination_last4 = models.CharField(max_length=4, blank=True)
    payload = models.JSONField(default=dict)
    provider = models.CharField(max_length=40, default="DEMO")
    provider_reference = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class DemoMessage(models.Model):
    """외부 SMS 없이 PDF MVP의 Demo Inbox를 재현하는 메시지 보관함."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="demo_messages")
    channel = models.CharField(max_length=20, default="SMS")
    destination_last4 = models.CharField(max_length=4, blank=True)
    body = models.TextField()
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "demo_messages"


class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="audit_logs")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    action = models.CharField(max_length=100)
    resource_type = models.CharField(max_length=80)
    resource_id = models.CharField(max_length=80, blank=True)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    request_id = models.CharField(max_length=80, blank=True)
    ip_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["store", "created_at"])]


class PrivacyRetentionPolicy(models.Model):
    store = models.OneToOneField(
        Store, on_delete=models.CASCADE, related_name="privacy_retention"
    )
    phone_retention_days = models.PositiveIntegerField(default=30)
    verification_retention_days = models.PositiveIntegerField(default=7)
    audit_retention_days = models.PositiveIntegerField(default=365)
    notice_text = models.TextField(
        default=(
            "전화번호는 Wi-Fi 이용권 연결과 부정 이용 대응을 위해 암호화·해시 처리되며, "
            "매장 정책의 보존 기간 이후 자동 폐기됩니다."
        )
    )
    updated_at = models.DateTimeField(auto_now=True)


class OutboxEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="outbox_events")
    type = models.CharField(max_length=100)
    aggregate_type = models.CharField(max_length=80)
    aggregate_id = models.CharField(max_length=80)
    payload = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["store", "occurred_at"]),
            models.Index(fields=["published_at", "occurred_at"]),
        ]
