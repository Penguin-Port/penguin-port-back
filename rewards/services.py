from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from operations.services import emit_event
from orders.models import Order
from rewards.models import (
    Coupon,
    DailySpendBalance,
    ImmediateBenefitRedemption,
    RewardGrant,
    RewardTier,
    RewardTierBenefit,
)


@dataclass(frozen=True)
class RewardEvaluation:
    daily_total: int
    new_grants: list[RewardGrant]


@transaction.atomic
def apply_paid_order(order: Order) -> RewardEvaluation:
    balance, _ = DailySpendBalance.objects.select_for_update().get_or_create(
        store=order.store,
        business_date=order.business_date,
        customer_key=order.customer_key,
        defaults={"total_amount": 0},
    )
    DailySpendBalance.objects.filter(pk=balance.pk).update(
        total_amount=F("total_amount") + order.total_amount,
        version=F("version") + 1,
    )
    balance.refresh_from_db()

    reached_tiers = RewardTier.objects.filter(
        store=order.store,
        is_active=True,
        threshold_amount__lte=balance.total_amount,
    ).order_by("sort_order", "threshold_amount")
    grants = []
    for tier in reached_tiers:
        grant, created = RewardGrant.objects.get_or_create(
            store=order.store,
            business_date=order.business_date,
            customer_key=order.customer_key,
            tier=tier,
        )
        if created:
            grants.append(grant)
            emit_event(
                store=order.store,
                type="reward.tier.achieved",
                aggregate_type="RewardGrant",
                aggregate_id=grant.id,
                payload={
                    "grantId": str(grant.id),
                    "tierAmount": tier.threshold_amount,
                    "businessDate": str(order.business_date),
                },
            )
    return RewardEvaluation(daily_total=balance.total_amount, new_grants=grants)


@transaction.atomic
def choose_benefit(
    *,
    grant_id,
    customer_key: str,
    benefit_id,
    fulfill_mode: str,
) -> tuple[RewardGrant, Coupon | None]:
    grant = (
        RewardGrant.objects.select_for_update()
        .select_related("tier", "store")
        .get(id=grant_id, customer_key=customer_key)
    )
    if grant.status != RewardGrant.Status.AWAITING_CHOICE:
        raise ValueError("이미 선택이 완료되었거나 선택할 수 없는 리워드입니다.")

    benefit = RewardTierBenefit.objects.get(
        id=benefit_id,
        tier=grant.tier,
        is_active=True,
    )
    if fulfill_mode not in RewardGrant.FulfillMode.values:
        raise ValueError("지원하지 않는 fulfillMode입니다.")

    grant.chosen_benefit = benefit
    grant.fulfill_mode = fulfill_mode
    grant.status = RewardGrant.Status.FULFILLED
    grant.fulfilled_at = timezone.now()
    grant.save(
        update_fields=[
            "chosen_benefit",
            "fulfill_mode",
            "status",
            "fulfilled_at",
        ]
    )

    coupon = None
    immediate_redemption = None
    if fulfill_mode == RewardGrant.FulfillMode.COUPON_7D:
        coupon = Coupon.objects.create(
            store=grant.store,
            customer_key=grant.customer_key,
            reward_grant=grant,
            benefit_snapshot={
                "benefitId": str(benefit.id),
                "type": benefit.benefit_type,
                "title": benefit.title,
                "payload": benefit.payload,
            },
            expires_at=timezone.now() + timedelta(days=7),
        )
    elif benefit.benefit_type == RewardTierBenefit.BenefitType.WIFI_DAY_PASS:
        from wifi.models import WiFiPass
        from wifi.services import apply_day_pass

        wifi_pass = (
            WiFiPass.objects.select_for_update()
            .select_related("store")
            .filter(
                store=grant.store,
                customer_key=grant.customer_key,
                business_date=grant.business_date,
            )
            .first()
        )
        if wifi_pass is None:
            raise ValueError("종일권을 적용할 Wi-Fi 이용권이 없습니다.")
        apply_day_pass(wifi_pass)
        immediate_redemption = ImmediateBenefitRedemption.objects.create(
            reward_grant=grant,
            status=ImmediateBenefitRedemption.Status.APPLIED,
            benefit_snapshot={
                "benefitId": str(benefit.id),
                "type": benefit.benefit_type,
                "title": benefit.title,
                "payload": benefit.payload,
            },
            expires_at=wifi_pass.expires_at,
            applied_at=timezone.now(),
        )
    elif fulfill_mode == RewardGrant.FulfillMode.IMMEDIATE:
        immediate_redemption = ImmediateBenefitRedemption.objects.create(
            reward_grant=grant,
            benefit_snapshot={
                "benefitId": str(benefit.id),
                "type": benefit.benefit_type,
                "title": benefit.title,
                "payload": benefit.payload,
            },
            expires_at=timezone.now() + timedelta(days=1),
        )
    emit_event(
        store=grant.store,
        type="reward.benefit.chosen",
        aggregate_type="RewardGrant",
        aggregate_id=grant.id,
        payload={
            "grantId": str(grant.id),
            "benefitId": str(benefit.id),
            "fulfillMode": fulfill_mode,
            "couponId": str(coupon.id) if coupon else None,
        },
    )
    return grant, coupon, immediate_redemption


def get_upsell_hint(*, store_id, customer_key: str):
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from stores.models import Store

    store = Store.objects.get(id=store_id)
    local_now = timezone.now().astimezone(ZoneInfo(store.timezone))
    business_date = local_now.date()
    if local_now.timetz().replace(tzinfo=None) < store.business_day_cutoff:
        business_date -= timedelta(days=1)
    today_balance = (
        DailySpendBalance.objects.filter(
            store_id=store_id,
            business_date=business_date,
            customer_key=customer_key,
        )
        .first()
    )
    daily_total = today_balance.total_amount if today_balance else 0
    next_tier = (
        RewardTier.objects.filter(
            store_id=store_id,
            is_active=True,
            threshold_amount__gt=daily_total,
        )
        .order_by("threshold_amount")
        .first()
    )
    if next_tier is None:
        return {
            "dailyTotal": daily_total,
            "nextTierAmount": None,
            "remainingAmountToNextTier": 0,
            "nextTierBenefitsPreview": [],
        }
    return {
        "dailyTotal": daily_total,
        "nextTierAmount": next_tier.threshold_amount,
        "remainingAmountToNextTier": next_tier.threshold_amount - daily_total,
        "nextTierBenefitsPreview": list(
            next_tier.benefits.filter(is_active=True).values_list("benefit_type", flat=True)
        ),
    }


@transaction.atomic
def redeem_coupon(*, coupon_id, customer_key: str):
    coupon = (
        Coupon.objects.select_for_update()
        .select_related("store", "reward_grant")
        .get(id=coupon_id, customer_key=customer_key)
    )
    if coupon.status != Coupon.Status.AVAILABLE:
        raise ValueError("사용할 수 없는 쿠폰입니다.")
    if coupon.expires_at <= timezone.now():
        coupon.status = Coupon.Status.EXPIRED
        coupon.save(update_fields=["status"])
        raise ValueError("쿠폰이 만료되었습니다.")
    coupon.status = Coupon.Status.REDEEMED
    coupon.redeemed_at = timezone.now()
    coupon.save(update_fields=["status", "redeemed_at"])
    emit_event(
        store=coupon.store,
        type="coupon.redeemed",
        aggregate_type="Coupon",
        aggregate_id=coupon.id,
        payload={"couponId": str(coupon.id), "redeemedAt": coupon.redeemed_at.isoformat()},
    )
    return coupon


@transaction.atomic
def apply_immediate_redemption(*, redemption_id, order_id, store_id):
    redemption = (
        ImmediateBenefitRedemption.objects.select_for_update()
        .select_related("reward_grant")
        .get(id=redemption_id, reward_grant__store_id=store_id)
    )
    if redemption.status != ImmediateBenefitRedemption.Status.AVAILABLE:
        raise ValueError("적용할 수 없는 즉시 혜택입니다.")
    if redemption.expires_at <= timezone.now():
        redemption.status = ImmediateBenefitRedemption.Status.EXPIRED
        redemption.save(update_fields=["status"])
        raise ValueError("즉시 혜택이 만료되었습니다.")
    order = Order.objects.get(
        id=order_id,
        store_id=store_id,
        customer_key=redemption.reward_grant.customer_key,
    )
    redemption.status = ImmediateBenefitRedemption.Status.APPLIED
    redemption.applied_order = order
    redemption.applied_at = timezone.now()
    redemption.save(update_fields=["status", "applied_order", "applied_at"])
    return redemption
