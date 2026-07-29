import uuid

from django.db import models

from catalog.models import Product
from stores.models import Store


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PAID = "PAID", "Paid"
        CANCELLED = "CANCELLED", "Cancelled"
        REFUNDED = "REFUNDED", "Refunded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="orders")
    external_order_id = models.CharField(max_length=120)
    customer_key = models.CharField(max_length=128, db_index=True)
    phone_last4 = models.CharField(max_length=4, blank=True)
    phone_ciphertext = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PAID)
    total_amount = models.PositiveIntegerField()
    refunded_amount = models.PositiveIntegerField(default=0)
    business_date = models.DateField(db_index=True)
    paid_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["store", "external_order_id"], name="unique_store_external_order"
            ),
            models.CheckConstraint(
                condition=models.Q(refunded_amount__lte=models.F("total_amount")),
                name="refund_not_greater_than_total",
            ),
        ]


class OrderItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    name_snapshot = models.CharField(max_length=120)
    quantity = models.PositiveIntegerField()
    unit_price = models.PositiveIntegerField()


class IdempotencyRecord(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE)
    key = models.CharField(max_length=120)
    request_hash = models.CharField(max_length=64)
    response_body = models.JSONField(default=dict)
    response_status = models.PositiveSmallIntegerField(default=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["store", "key"], name="unique_store_idempotency")
        ]


class OrderClaim(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="claim")
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    exchanged_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
