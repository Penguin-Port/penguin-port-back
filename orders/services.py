import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from catalog.models import Product
from operations.services import emit_event
from orders.crypto import encrypt_phone
from orders.models import Order, OrderClaim, OrderItem
from rewards.models import Coupon, DailySpendBalance, RewardGrant
from rewards.services import RewardEvaluation, apply_paid_order
from stores.models import Store
from wifi.services import WiFiResult, issue_or_extend_pass


def normalize_phone(phone: str) -> str:
    return "".join(character for character in phone if character.isdigit())


def phone_customer_key(phone: str) -> str:
    normalized = normalize_phone(phone)
    if len(normalized) < 10:
        raise ValueError("유효한 전화번호가 필요합니다.")
    digest = hmac.new(
        settings.SECRET_KEY.encode(),
        normalized.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()
    return f"phone:{digest}"


def resolve_customer_key(member_id: str | None, phone: str | None) -> str:
    if member_id:
        return f"member:{member_id}"
    if phone:
        return phone_customer_key(phone)
    raise ValueError("memberId 또는 phone이 필요합니다.")


def get_business_date(store: Store, occurred_at: datetime):
    local_time = occurred_at.astimezone(ZoneInfo(store.timezone))
    business_date = local_time.date()
    if local_time.timetz().replace(tzinfo=None) < store.business_day_cutoff:
        business_date -= timedelta(days=1)
    return business_date


@dataclass(frozen=True)
class PaidOrderResult:
    order: Order
    reward: RewardEvaluation
    wifi: WiFiResult
    order_claim_token: str
    order_claim_expires_at: datetime


@transaction.atomic
def create_paid_order(
    *,
    store: Store,
    external_order_id: str,
    member_id: str | None,
    phone: str | None,
    items: list[dict],
    total_amount: int,
    paid_at: datetime,
) -> PaidOrderResult:
    if total_amount < 0:
        raise ValueError("totalAmount는 0 이상이어야 합니다.")
    if not items:
        raise ValueError("items는 한 개 이상이어야 합니다.")

    customer_key = resolve_customer_key(member_id, phone)
    business_date = get_business_date(store, paid_at)
    products = Product.objects.in_bulk([item["product_id"] for item in items])

    calculated_total = 0
    normalized_items = []
    for item in items:
        product_id = item["product_id"]
        product = products.get(product_id)
        if product is None or product.store_id != store.id or not product.is_active:
            raise ValueError(f"주문할 수 없는 상품입니다: {product_id}")
        quantity = int(item["quantity"])
        unit_price = int(item.get("unit_price", product.price))
        if quantity <= 0 or unit_price < 0:
            raise ValueError("quantity와 unitPrice가 올바르지 않습니다.")
        calculated_total += quantity * unit_price
        normalized_items.append((product, quantity, unit_price))

    if calculated_total != total_amount:
        raise ValueError("품목 합계와 totalAmount가 일치하지 않습니다.")

    order = Order.objects.create(
        store=store,
        external_order_id=external_order_id,
        customer_key=customer_key,
        phone_last4=normalize_phone(phone)[-4:] if phone else "",
        phone_ciphertext=encrypt_phone(phone),
        status=Order.Status.PAID,
        total_amount=total_amount,
        business_date=business_date,
        paid_at=paid_at,
    )
    OrderItem.objects.bulk_create(
        [
            OrderItem(
                order=order,
                product=product,
                name_snapshot=product.name,
                quantity=quantity,
                unit_price=unit_price,
            )
            for product, quantity, unit_price in normalized_items
        ]
    )

    reward = apply_paid_order(order)
    wifi = issue_or_extend_pass(order)

    raw_claim = secrets.token_urlsafe(32)
    claim_expires_at = timezone.now() + timedelta(minutes=10)
    OrderClaim.objects.create(
        order=order,
        token_hash=hashlib.sha256(raw_claim.encode()).hexdigest(),
        expires_at=claim_expires_at,
    )
    emit_event(
        store=store,
        type="order.paid",
        aggregate_type="Order",
        aggregate_id=order.id,
        payload={
            "orderId": str(order.id),
            "dailyTotal": reward.daily_total,
            "wifiPassId": str(wifi.wifi_pass.id),
            "newRewardGrantIds": [str(grant.id) for grant in reward.new_grants],
        },
    )
    return PaidOrderResult(
        order=order,
        reward=reward,
        wifi=wifi,
        order_claim_token=raw_claim,
        order_claim_expires_at=claim_expires_at,
    )


@transaction.atomic
def refund_order(*, order_id, store: Store, refund_amount: int):
    order = Order.objects.select_for_update().get(id=order_id, store=store)
    remaining = order.total_amount - order.refunded_amount
    if refund_amount <= 0 or refund_amount > remaining:
        raise ValueError("환불 금액이 결제 잔액을 초과하거나 올바르지 않습니다.")

    order.refunded_amount += refund_amount
    order.status = (
        Order.Status.REFUNDED
        if order.refunded_amount == order.total_amount
        else Order.Status.PAID
    )
    order.save(update_fields=["refunded_amount", "status", "updated_at"])

    balance = DailySpendBalance.objects.select_for_update().get(
        store=store,
        business_date=order.business_date,
        customer_key=order.customer_key,
    )
    balance.total_amount = max(0, balance.total_amount - refund_amount)
    balance.version += 1
    balance.save(update_fields=["total_amount", "version", "updated_at"])

    revoked_grants = []
    grants = RewardGrant.objects.select_for_update().filter(
        store=store,
        business_date=order.business_date,
        customer_key=order.customer_key,
        tier__threshold_amount__gt=balance.total_amount,
    )
    for grant in grants:
        if grant.status == RewardGrant.Status.AWAITING_CHOICE:
            grant.status = RewardGrant.Status.REVOKED
            grant.save(update_fields=["status"])
            revoked_grants.append(str(grant.id))
        elif grant.status == RewardGrant.Status.FULFILLED:
            Coupon.objects.filter(
                reward_grant=grant,
                status=Coupon.Status.AVAILABLE,
            ).update(status=Coupon.Status.REVOKED)

    emit_event(
        store=store,
        type="order.refunded",
        aggregate_type="Order",
        aggregate_id=order.id,
        payload={
            "orderId": str(order.id),
            "refundAmount": refund_amount,
            "dailyTotal": balance.total_amount,
            "revokedRewardGrantIds": revoked_grants,
        },
    )
    return order, balance, revoked_grants
