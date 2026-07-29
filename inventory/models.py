import uuid

from django.db import models

from catalog.models import Product
from stores.models import Store


class InventoryItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="inventory_items")
    product = models.OneToOneField(
        Product, on_delete=models.CASCADE, related_name="inventory"
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    unit = models.CharField(max_length=20, default="EA")
    low_stock_threshold = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    expires_on = models.DateField(null=True, blank=True)
    risk_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["store", "expires_on"]),
            models.Index(fields=["store", "risk_score"]),
        ]


class InventoryEvent(models.Model):
    class Type(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        CONSUMED = "CONSUMED", "Consumed"
        ADJUSTED = "ADJUSTED", "Adjusted"
        RISK_DETECTED = "RISK_DETECTED", "Risk detected"
        EXPIRED = "EXPIRED", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="events")
    type = models.CharField(max_length=24, choices=Type.choices)
    quantity_delta = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    reason = models.CharField(max_length=240, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)
