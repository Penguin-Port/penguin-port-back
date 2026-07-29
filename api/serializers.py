from rest_framework import serializers

from rewards.models import RewardGrant


class OrderItemInputSerializer(serializers.Serializer):
    productId = serializers.UUIDField(source="product_id")
    quantity = serializers.IntegerField(min_value=1)
    unitPrice = serializers.IntegerField(source="unit_price", min_value=0, required=False)


class CustomerInputSerializer(serializers.Serializer):
    memberId = serializers.CharField(source="member_id", required=False, allow_null=True)
    phone = serializers.CharField(required=False, allow_null=True)

    def validate(self, attrs):
        if not attrs.get("member_id") and not attrs.get("phone"):
            raise serializers.ValidationError("memberId 또는 phone이 필요합니다.")
        return attrs


class PaidOrderInputSerializer(serializers.Serializer):
    storeId = serializers.UUIDField(source="store_id")
    externalOrderId = serializers.CharField(source="external_order_id", max_length=120)
    customer = CustomerInputSerializer()
    items = OrderItemInputSerializer(many=True, allow_empty=False)
    totalAmount = serializers.IntegerField(source="total_amount", min_value=0)
    paidAt = serializers.DateTimeField(source="paid_at")


class OrderClaimExchangeSerializer(serializers.Serializer):
    orderClaim = serializers.CharField(source="order_claim")


class RewardChooseSerializer(serializers.Serializer):
    benefitId = serializers.UUIDField(source="benefit_id")
    fulfillMode = serializers.ChoiceField(
        source="fulfill_mode", choices=RewardGrant.FulfillMode.choices
    )


class RecommendationAcceptSerializer(serializers.Serializer):
    storeId = serializers.UUIDField(source="store_id")
    version = serializers.IntegerField(min_value=1)
    title = serializers.CharField(max_length=160)
    payload = serializers.JSONField()
    startsAt = serializers.DateTimeField(source="starts_at")
    endsAt = serializers.DateTimeField(source="ends_at")


class VerificationStartSerializer(serializers.Serializer):
    verificationTicket = serializers.CharField(source="verification_ticket")
    phone = serializers.CharField()


class VerificationConfirmSerializer(serializers.Serializer):
    challengeId = serializers.UUIDField(source="challenge_id")
    code = serializers.RegexField(r"^\d{6}$")


class RefundSerializer(serializers.Serializer):
    storeId = serializers.UUIDField(source="store_id")
    refundAmount = serializers.IntegerField(source="refund_amount", min_value=1)


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False)


class WiFiPolicySerializer(serializers.Serializer):
    storeId = serializers.UUIDField(source="store_id")
    baseMinutes = serializers.IntegerField(source="base_minutes", min_value=1)
    quietHoursEnabled = serializers.BooleanField(
        source="quiet_hours_enabled", default=False
    )
    quietHoursUntil = serializers.TimeField(
        source="quiet_hours_until", required=False, allow_null=True
    )
    amountTiers = serializers.ListField(
        source="amount_tiers", child=serializers.DictField(), allow_empty=True
    )

    def validate_amount_tiers(self, value):
        for tier in value:
            if tier.get("orderType") not in ["FIRST", "ADDITIONAL"]:
                raise serializers.ValidationError("orderType이 올바르지 않습니다.")
            if int(tier.get("minAmount", -1)) < 0 or int(tier.get("bonusMinutes", -1)) < 0:
                raise serializers.ValidationError("금액과 시간은 0 이상이어야 합니다.")
        return value


class WiFiPolicySimulateSerializer(serializers.Serializer):
    storeId = serializers.UUIDField(source="store_id")
    orderType = serializers.ChoiceField(
        source="order_type", choices=["FIRST", "ADDITIONAL"]
    )
    amount = serializers.IntegerField(min_value=0)


class InventoryCreateSerializer(serializers.Serializer):
    storeId = serializers.UUIDField(source="store_id")
    productId = serializers.UUIDField(source="product_id")
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2)
    unit = serializers.CharField(max_length=20, default="EA")
    lowStockThreshold = serializers.DecimalField(
        source="low_stock_threshold", max_digits=12, decimal_places=2, default=0
    )
    expiresOn = serializers.DateField(source="expires_on", required=False, allow_null=True)


class InventoryAdjustSerializer(serializers.Serializer):
    quantityDelta = serializers.DecimalField(
        source="quantity_delta", max_digits=12, decimal_places=2
    )
    reason = serializers.CharField(max_length=240)


class RecommendationRejectSerializer(serializers.Serializer):
    storeId = serializers.UUIDField(source="store_id")
    reason = serializers.CharField(required=False, allow_blank=True)


class RewardTierCreateSerializer(serializers.Serializer):
    storeId = serializers.UUIDField(source="store_id")
    name = serializers.CharField(max_length=80)
    thresholdAmount = serializers.IntegerField(source="threshold_amount", min_value=1)
    sortOrder = serializers.IntegerField(source="sort_order", min_value=0)
    benefits = serializers.ListField(child=serializers.DictField(), allow_empty=False)


class PrivacyRetentionSerializer(serializers.Serializer):
    phoneRetentionDays = serializers.IntegerField(
        source="phone_retention_days", min_value=1, required=False
    )
    verificationRetentionDays = serializers.IntegerField(
        source="verification_retention_days", min_value=1, required=False
    )
    auditRetentionDays = serializers.IntegerField(
        source="audit_retention_days", min_value=1, required=False
    )
    noticeText = serializers.CharField(source="notice_text", required=False)


class ProductCreateSerializer(serializers.Serializer):
    storeId = serializers.UUIDField(source="store_id")
    categoryId = serializers.UUIDField(source="category_id")
    name = serializers.CharField(max_length=120)
    price = serializers.IntegerField(min_value=0)


class ManualPassActionSerializer(serializers.Serializer):
    storeId = serializers.UUIDField(source="store_id")
    action = serializers.ChoiceField(choices=["EXTEND", "BLOCK", "UNBLOCK"])
    minutes = serializers.IntegerField(min_value=1, required=False)

    def validate(self, attrs):
        if attrs["action"] == "EXTEND" and not attrs.get("minutes"):
            raise serializers.ValidationError("EXTEND에는 minutes가 필요합니다.")
        return attrs


class RecommendationEditSerializer(serializers.Serializer):
    storeId = serializers.UUIDField(source="store_id")
    version = serializers.IntegerField(min_value=1)
    payload = serializers.JSONField()
    reason = serializers.CharField(required=False)


class PromotionUpdateSerializer(serializers.Serializer):
    storeId = serializers.UUIDField(source="store_id")
    title = serializers.CharField(max_length=160, required=False)
    payload = serializers.JSONField(required=False)
    startsAt = serializers.DateTimeField(source="starts_at", required=False)
    endsAt = serializers.DateTimeField(source="ends_at", required=False)
    status = serializers.ChoiceField(
        choices=["DRAFT", "SCHEDULED", "ACTIVE", "ENDED"], required=False
    )


class MembershipUpsertSerializer(serializers.Serializer):
    storeId = serializers.UUIDField(source="store_id")
    userId = serializers.IntegerField(source="user_id")
    role = serializers.ChoiceField(choices=["OWNER", "MANAGER", "STAFF", "VIEWER"])


class StoreSettingsSerializer(serializers.Serializer):
    storeId = serializers.UUIDField(source="store_id")
    name = serializers.CharField(max_length=120, required=False)
    timezone = serializers.CharField(max_length=64, required=False)
    businessDayCutoff = serializers.TimeField(
        source="business_day_cutoff", required=False
    )
    segment = serializers.ChoiceField(
        choices=["UNIVERSITY", "FRANCHISE"], required=False
    )
    otpSkipEnabled = serializers.BooleanField(
        source="otp_skip_enabled", required=False
    )


class ImmediateBenefitApplySerializer(serializers.Serializer):
    storeId = serializers.UUIDField(source="store_id")
    orderId = serializers.UUIDField(source="order_id")
