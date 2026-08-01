import uuid

from django.db import models

from orders.models import Order
from stores.models import Store


class WiFiPolicy(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="wifi_policies")
    version = models.PositiveIntegerField()
    base_minutes = models.PositiveIntegerField(default=120)
    quiet_hours_enabled = models.BooleanField(default=False)
    quiet_hours_until = models.TimeField(null=True, blank=True)
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["store", "version"], name="unique_store_wifi_policy_version"
            )
        ]


class WiFiAmountTier(models.Model):
    class OrderType(models.TextChoices):
        FIRST = "FIRST", "First order"
        ADDITIONAL = "ADDITIONAL", "Additional order"

    policy = models.ForeignKey(WiFiPolicy, on_delete=models.CASCADE, related_name="amount_tiers")
    order_type = models.CharField(max_length=20, choices=OrderType.choices)
    min_amount = models.PositiveIntegerField()
    bonus_minutes = models.PositiveIntegerField()

    class Meta:
        ordering = ["min_amount"]
        constraints = [
            models.UniqueConstraint(
                fields=["policy", "order_type", "min_amount"],
                name="unique_policy_order_type_amount",
            )
        ]


class WiFiPass(models.Model):
    class Status(models.TextChoices):
        ISSUED = "ISSUED", "Issued"
        ACTIVATING = "ACTIVATING", "Activating"
        ACTIVE = "ACTIVE", "Active"
        EXPIRING_SOON = "EXPIRING_SOON", "Expiring soon"
        EXPIRED = "EXPIRED", "Expired"
        CANCELLED = "CANCELLED", "Cancelled"
        BLOCKED = "BLOCKED", "Blocked"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="wifi_passes")
    customer_key = models.CharField(max_length=128, db_index=True)
    business_date = models.DateField(db_index=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.ISSUED)
    issued_at = models.DateTimeField()
    activated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    policy_version = models.PositiveIntegerField()
    policy_snapshot = models.JSONField(default=dict)
    pass_version = models.PositiveIntegerField(default=1)
    network_reference = models.CharField(max_length=200, null=True, blank=True)


class PassExtension(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wifi_pass = models.ForeignKey(WiFiPass, on_delete=models.CASCADE, related_name="extensions")
    order = models.ForeignKey(Order, on_delete=models.PROTECT)
    minutes = models.PositiveIntegerField()
    reason = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)


class ScheduledAction(models.Model):
    class ActionType(models.TextChoices):
        EXPIRE_PASS = "EXPIRE_PASS", "Expire pass"
        SEND_EXPIRING_NOTIFICATION = "SEND_EXPIRING_NOTIFICATION", "Send notification"
        REVOKE_NETWORK_ACCESS = "REVOKE_NETWORK_ACCESS", "Revoke network"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wifi_pass = models.ForeignKey(
        WiFiPass, on_delete=models.CASCADE, related_name="scheduled_actions"
    )
    action_type = models.CharField(max_length=40, choices=ActionType.choices)
    execute_at = models.DateTimeField()
    pass_version = models.PositiveIntegerField()
    completed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
