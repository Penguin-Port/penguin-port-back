import uuid

from django.db import models

from stores.models import Store


class AIRecommendation(models.Model):
    class Type(models.TextChoices):
        SALES_SUMMARY = "SALES_SUMMARY", "Sales summary"
        TIME_SALE = "TIME_SALE", "Time sale"
        INVENTORY_PROMOTION = "INVENTORY_PROMOTION", "Inventory promotion"
        MENU_TREND = "MENU_TREND", "Menu trend"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACCEPTED = "ACCEPTED", "Accepted"
        REJECTED = "REJECTED", "Rejected"
        EDITED = "EDITED", "Edited"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="recommendations")
    type = models.CharField(max_length=40, choices=Type.choices)
    payload = models.JSONField(default=dict)
    reason = models.TextField()
    evidence = models.JSONField(default=dict)
    confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)


class Promotion(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SCHEDULED = "SCHEDULED", "Scheduled"
        ACTIVE = "ACTIVE", "Active"
        ENDED = "ENDED", "Ended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="promotions")
    source_recommendation = models.OneToOneField(
        AIRecommendation, on_delete=models.PROTECT, related_name="promotion"
    )
    title = models.CharField(max_length=160)
    payload = models.JSONField(default=dict)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    created_at = models.DateTimeField(auto_now_add=True)


class AnalyticsHourly(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="hourly_analytics")
    bucket_start = models.DateTimeField()
    order_count = models.PositiveIntegerField(default=0)
    gross_sales = models.PositiveIntegerField(default=0)
    wifi_active_count = models.PositiveIntegerField(default=0)
    wifi_active_minutes = models.PositiveIntegerField(default=0)
    menu_sales = models.JSONField(default=dict)
    repeat_customer_count = models.PositiveIntegerField(default=0)
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["store", "bucket_start"], name="unique_store_hourly_bucket"
            )
        ]
        indexes = [models.Index(fields=["store", "bucket_start"])]
