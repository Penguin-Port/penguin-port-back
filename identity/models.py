import uuid

from django.conf import settings
from django.db import models

from orders.models import Order
from stores.models import Store


class VerificationChallenge(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VERIFIED = "VERIFIED", "Verified"
        EXPIRED = "EXPIRED", "Expired"
        LOCKED = "LOCKED", "Locked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="verifications")
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="verifications")
    customer_key = models.CharField(max_length=128, db_index=True)
    phone_last4 = models.CharField(max_length=4)
    code_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["store", "customer_key", "created_at"]),
            models.Index(fields=["status", "expires_at"]),
        ]


class RefreshTokenSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="refresh_sessions",
    )
    jti_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    rotated_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "expires_at"])]
