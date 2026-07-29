import uuid

from django.db import models

from stores.models import Store


class DailySpendBalance(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="daily_balances")
    business_date = models.DateField()
    customer_key = models.CharField(max_length=128)
    total_amount = models.PositiveIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["store", "business_date", "customer_key"],
                name="unique_daily_spend_balance",
            )
        ]


class RewardTier(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="reward_tiers")
    name = models.CharField(max_length=80)
    threshold_amount = models.PositiveIntegerField()
    sort_order = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "threshold_amount"]
        constraints = [
            models.UniqueConstraint(
                fields=["store", "threshold_amount"], name="unique_store_reward_threshold"
            )
        ]


class RewardTierBenefit(models.Model):
    class BenefitType(models.TextChoices):
        DISCOUNT_COUPON = "DISCOUNT_COUPON", "Discount coupon"
        MILEAGE = "MILEAGE", "Mileage"
        FREE_SHOT = "FREE_SHOT", "Free shot"
        FREE_SIZE_UP = "FREE_SIZE_UP", "Free size up"
        DESSERT_DISCOUNT = "DESSERT_DISCOUNT", "Dessert discount"
        WIFI_DAY_PASS = "WIFI_DAY_PASS", "Wi-Fi day pass"
        DRINK_DISCOUNT = "DRINK_DISCOUNT", "Drink discount"
        MENU_TRIAL = "MENU_TRIAL", "Menu trial"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tier = models.ForeignKey(RewardTier, on_delete=models.CASCADE, related_name="benefits")
    benefit_type = models.CharField(max_length=40, choices=BenefitType.choices)
    title = models.CharField(max_length=120)
    payload = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)


class RewardGrant(models.Model):
    class Status(models.TextChoices):
        AWAITING_CHOICE = "AWAITING_CHOICE", "Awaiting choice"
        FULFILLED = "FULFILLED", "Fulfilled"
        REVOKED = "REVOKED", "Revoked"
        EXPIRED = "EXPIRED", "Expired"

    class FulfillMode(models.TextChoices):
        IMMEDIATE = "IMMEDIATE", "Immediate"
        COUPON_7D = "COUPON_7D", "Coupon for seven days"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="reward_grants")
    business_date = models.DateField()
    customer_key = models.CharField(max_length=128)
    tier = models.ForeignKey(RewardTier, on_delete=models.PROTECT)
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.AWAITING_CHOICE
    )
    chosen_benefit = models.ForeignKey(
        RewardTierBenefit, null=True, blank=True, on_delete=models.PROTECT
    )
    fulfill_mode = models.CharField(
        max_length=20, choices=FulfillMode.choices, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    fulfilled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["store", "business_date", "customer_key", "tier"],
                name="unique_daily_tier_grant",
            )
        ]


class Coupon(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        REDEEMED = "REDEEMED", "Redeemed"
        EXPIRED = "EXPIRED", "Expired"
        REVOKED = "REVOKED", "Revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="coupons")
    customer_key = models.CharField(max_length=128, db_index=True)
    reward_grant = models.OneToOneField(
        RewardGrant, on_delete=models.PROTECT, related_name="coupon"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.AVAILABLE
    )
    benefit_snapshot = models.JSONField(default=dict)
    expires_at = models.DateTimeField()
    redeemed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ImmediateBenefitRedemption(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        APPLIED = "APPLIED", "Applied"
        EXPIRED = "EXPIRED", "Expired"
        REVOKED = "REVOKED", "Revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reward_grant = models.OneToOneField(
        RewardGrant, on_delete=models.PROTECT, related_name="immediate_redemption"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.AVAILABLE
    )
    benefit_snapshot = models.JSONField(default=dict)
    applied_order = models.ForeignKey(
        "orders.Order", null=True, blank=True, on_delete=models.PROTECT
    )
    expires_at = models.DateTimeField()
    applied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
