from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from operations.services import emit_event
from orders.models import Order
from wifi.adapters import get_network_adapter
from wifi.models import PassExtension, ScheduledAction, WiFiAmountTier, WiFiPass, WiFiPolicy


@dataclass(frozen=True)
class WiFiResult:
    wifi_pass: WiFiPass
    extension_minutes: int
    breakdown: list[dict]


def _published_policy(order: Order) -> WiFiPolicy:
    policy = (
        WiFiPolicy.objects.filter(store=order.store, is_published=True)
        .order_by("-version")
        .first()
    )
    if policy is None:
        raise ValueError("게시된 Wi-Fi 정책이 없습니다.")
    return policy


def _tier_minutes(policy: WiFiPolicy, order_type: str, amount: int) -> int:
    tier = (
        policy.amount_tiers.filter(order_type=order_type, min_amount__lte=amount)
        .order_by("-min_amount")
        .first()
    )
    return tier.bonus_minutes if tier else 0


@transaction.atomic
def issue_or_extend_pass(order: Order) -> WiFiResult:
    policy = _published_policy(order)
    existing = (
        WiFiPass.objects.select_for_update()
        .filter(
            store=order.store,
            customer_key=order.customer_key,
            business_date=order.business_date,
        )
        .exclude(
            status__in=[
                WiFiPass.Status.CANCELLED,
                WiFiPass.Status.BLOCKED,
                WiFiPass.Status.FAILED,
            ]
        )
        .first()
    )

    now = timezone.now()
    if existing is None:
        bonus = _tier_minutes(
            policy, WiFiAmountTier.OrderType.FIRST, order.total_amount
        )
        minutes = policy.base_minutes + bonus
        breakdown = [{"type": "BASE", "minutes": policy.base_minutes}]
        if bonus:
            breakdown.append({"type": "FIRST_ORDER_AMOUNT_BONUS", "minutes": bonus})
        wifi_pass = WiFiPass.objects.create(
            store=order.store,
            customer_key=order.customer_key,
            business_date=order.business_date,
            status=WiFiPass.Status.ISSUED,
            issued_at=now,
            expires_at=now + timedelta(minutes=minutes),
            policy_version=policy.version,
        )
        reason = "FIRST_ORDER"
    else:
        minutes = _tier_minutes(
            policy, WiFiAmountTier.OrderType.ADDITIONAL, order.total_amount
        )
        breakdown = [{"type": "ADDITIONAL_ORDER_EXTENSION", "minutes": minutes}]
        wifi_pass = existing
        extension_base = max(existing.expires_at, now)
        wifi_pass.expires_at = extension_base + timedelta(minutes=minutes)
        wifi_pass.pass_version += 1
        if wifi_pass.status == WiFiPass.Status.EXPIRED and minutes:
            wifi_pass.status = WiFiPass.Status.ACTIVE
        wifi_pass.save(update_fields=["expires_at", "pass_version", "status"])
        reason = "ADDITIONAL_ORDER"

    if policy.quiet_hours_enabled and policy.quiet_hours_until:
        local_now = now.astimezone(ZoneInfo(order.store.timezone))
        quiet_until_local = datetime.combine(
            local_now.date(),
            policy.quiet_hours_until,
            tzinfo=ZoneInfo(order.store.timezone),
        )
        if local_now < quiet_until_local:
            quiet_until_utc = quiet_until_local.astimezone(ZoneInfo("UTC"))
            if wifi_pass.expires_at < quiet_until_utc:
                quiet_minutes = int(
                    (quiet_until_utc - wifi_pass.expires_at).total_seconds() // 60
                )
                wifi_pass.expires_at = quiet_until_utc
                wifi_pass.save(update_fields=["expires_at"])
                minutes += quiet_minutes
                breakdown.append(
                    {"type": "QUIET_HOURS_AUTO_EXTENSION", "minutes": quiet_minutes}
                )

    PassExtension.objects.create(
        wifi_pass=wifi_pass,
        order=order,
        minutes=minutes,
        reason=reason,
    )
    ScheduledAction.objects.create(
        wifi_pass=wifi_pass,
        action_type=ScheduledAction.ActionType.EXPIRE_PASS,
        execute_at=wifi_pass.expires_at,
        pass_version=wifi_pass.pass_version,
    )
    notification_at = wifi_pass.expires_at - timedelta(minutes=10)
    if notification_at > now:
        ScheduledAction.objects.create(
            wifi_pass=wifi_pass,
            action_type=ScheduledAction.ActionType.SEND_EXPIRING_NOTIFICATION,
            execute_at=notification_at,
            pass_version=wifi_pass.pass_version,
        )
    ScheduledAction.objects.create(
        wifi_pass=wifi_pass,
        action_type=ScheduledAction.ActionType.REVOKE_NETWORK_ACCESS,
        execute_at=wifi_pass.expires_at,
        pass_version=wifi_pass.pass_version,
    )
    emit_event(
        store=order.store,
        type="wifi.pass.issued" if existing is None else "wifi.pass.extended",
        aggregate_type="WiFiPass",
        aggregate_id=wifi_pass.id,
        payload={
            "passId": str(wifi_pass.id),
            "minutes": minutes,
            "expiresAt": wifi_pass.expires_at.isoformat(),
            "passVersion": wifi_pass.pass_version,
        },
    )
    return WiFiResult(
        wifi_pass=wifi_pass,
        extension_minutes=minutes,
        breakdown=breakdown,
    )


@transaction.atomic
def activate_pass(*, pass_id, customer_key: str) -> WiFiPass:
    wifi_pass = WiFiPass.objects.select_for_update().get(
        id=pass_id, customer_key=customer_key
    )
    if wifi_pass.status not in [WiFiPass.Status.ISSUED, WiFiPass.Status.ACTIVATING]:
        raise ValueError("활성화할 수 없는 이용권 상태입니다.")
    if wifi_pass.expires_at <= timezone.now():
        wifi_pass.status = WiFiPass.Status.EXPIRED
        wifi_pass.save(update_fields=["status"])
        raise ValueError("이미 만료된 이용권입니다.")

    authorization = get_network_adapter().authorize(
        pass_id=wifi_pass.id, expires_at=wifi_pass.expires_at
    )
    wifi_pass.status = WiFiPass.Status.ACTIVE
    wifi_pass.activated_at = timezone.now()
    wifi_pass.network_reference = authorization.reference
    wifi_pass.save(update_fields=["status", "activated_at", "network_reference"])
    emit_event(
        store=wifi_pass.store,
        type="wifi.pass.activated",
        aggregate_type="WiFiPass",
        aggregate_id=wifi_pass.id,
        payload={
            "passId": str(wifi_pass.id),
            "expiresAt": wifi_pass.expires_at.isoformat(),
        },
    )
    return wifi_pass


@transaction.atomic
def apply_day_pass(wifi_pass: WiFiPass) -> WiFiPass:
    store_zone = ZoneInfo(wifi_pass.store.timezone)
    next_business_date = wifi_pass.business_date + timedelta(days=1)
    local_end = datetime.combine(
        next_business_date,
        wifi_pass.store.business_day_cutoff or time.min,
        tzinfo=store_zone,
    )
    wifi_pass.expires_at = local_end.astimezone(ZoneInfo("UTC"))
    wifi_pass.pass_version += 1
    wifi_pass.save(update_fields=["expires_at", "pass_version"])
    return wifi_pass
