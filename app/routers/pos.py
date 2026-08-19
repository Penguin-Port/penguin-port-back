import hashlib
import json
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_demo_key
from app.db import get_db
from app.http import success
from app.models import (
    IdempotencyRecord,
    Order,
    OrderClaim,
    OrderItem,
    Product,
    Coupon,
    DailySpendBalance,
    RewardGrant,
    RewardTier,
    RewardRedemption,
    Store,
    WiFiPass,
)
from app.schemas import PosOrderRequest, PosRefundRequest
from app.services.policy import additional_order_minutes, expiry_after, first_order_minutes
from app.services.rewards import evaluate_grants
from app.security import customer_key, phone_last4, phone_lookup_hash
from app.services.audit import record_audit
from app.services.events import publish_event
from app.services.wifi import (
    PASS_REACTIVATION_BLOCKED_STATUSES,
    pass_data,
    prorated_refunded_wifi_minutes,
    reclaim_wifi_minutes,
)
from app.time import business_date, db_now, normalize


router = APIRouter(tags=["POS"])


def _customer_key(payload: PosOrderRequest) -> str:
    try:
        return customer_key(member_id=payload.customer.memberId, phone=payload.customer.phone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _request_hash(payload: PosOrderRequest) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _payload_hash(payload) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _existing_idempotent_response(
    db: Session, *, scope: str, key: str, request_hash: str
) -> JSONResponse | None:
    record = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.key == key,
        )
    )
    if record is None:
        return None
    if record.request_hash != request_hash:
        raise HTTPException(
            status_code=409,
            detail="같은 Idempotency-Key로 다른 주문 요청을 재사용할 수 없습니다.",
        )
    return JSONResponse(content=record.response_json, status_code=record.status_code)


@router.post("/pos/orders", status_code=201)
def create_order(
    payload: PosOrderRequest,
    db: Session = Depends(get_db),
    _: None = Depends(require_demo_key),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    store = db.get(Store, payload.storeId)
    if store is None:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다.")
    request_hash = _request_hash(payload)
    scope = f"pos-order:{store.id}"
    if idempotency_key:
        if not 8 <= len(idempotency_key) <= 200:
            raise HTTPException(status_code=422, detail="Idempotency-Key 길이가 올바르지 않습니다.")
        replay = _existing_idempotent_response(
            db, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        if replay is not None:
            return replay
    duplicate = db.scalar(
        select(Order).where(
            Order.store_id == store.id,
            Order.external_order_id == payload.externalOrderId,
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="이미 처리된 externalOrderId입니다.")

    resolved_items = []
    calculated_total = 0
    for item in payload.items:
        product = db.get(Product, item.productId)
        if product is None or product.store_id != store.id or not product.is_active:
            raise HTTPException(status_code=422, detail="주문 상품을 찾을 수 없습니다.")
        unit_price = item.unitPrice if item.unitPrice is not None else product.price
        calculated_total += item.quantity * unit_price
        resolved_items.append((item, product, unit_price))
    if calculated_total != payload.totalAmount:
        raise HTTPException(
            status_code=422,
            detail="totalAmount가 주문 항목 합계와 일치하지 않습니다.",
        )

    now = db_now()
    customer_key = _customer_key(payload)
    day = business_date(
        payload.paidAt,
        timezone_name=store.timezone,
        cutoff=store.business_day_cutoff,
    )
    lookup_hash = phone_lookup_hash(payload.customer.phone) if payload.customer.phone else None
    last4 = phone_last4(payload.customer.phone) if payload.customer.phone else None
    wifi_pass = db.scalar(
        select(WiFiPass).where(
            WiFiPass.store_id == store.id,
            WiFiPass.customer_key == customer_key,
            WiFiPass.business_date == day,
        )
    )
    if wifi_pass is not None and wifi_pass.status in PASS_REACTIVATION_BLOCKED_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="현재 이용권 상태에서는 추가 주문으로 Wi-Fi 시간을 연장할 수 없습니다.",
        )
    if wifi_pass is None:
        minutes, snapshot = first_order_minutes(payload.totalAmount, store.policy_config)
        wifi_pass = WiFiPass(
            store_id=store.id,
            customer_key=customer_key,
            business_date=day,
            status="ISSUED",
            issued_at=now,
            expires_at=expiry_after(minutes, now),
            version=1,
            policy_snapshot=snapshot,
        )
        db.add(wifi_pass)
    else:
        minutes, snapshot = additional_order_minutes(payload.totalAmount, store.policy_config)
        wifi_pass.expires_at = max(normalize(wifi_pass.expires_at),normalize(now),) + timedelta(minutes=minutes)
        wifi_pass.version += 1
        wifi_pass.policy_snapshot = snapshot
        if wifi_pass.status == "EXPIRED":
            wifi_pass.status = "ACTIVE"
    db.flush()

    order = Order(
        store_id=store.id,
        external_order_id=payload.externalOrderId,
        customer_key=customer_key,
        phone=None,
        phone_lookup_hash=lookup_hash,
        phone_last4=last4,
        total_amount=payload.totalAmount,
        status="PAID",
        refunded_amount=0,
        wifi_minutes=minutes,
        business_date=day,
        paid_at=payload.paidAt,
    )
    db.add(order)
    db.flush()

    applied_rewards = []
    redemption = db.scalar(
        select(RewardRedemption)
        .where(
            RewardRedemption.store_id == store.id,
            RewardRedemption.customer_key == customer_key,
            RewardRedemption.business_date == day,
            RewardRedemption.status == "AVAILABLE",
        )
        .order_by(RewardRedemption.created_at.asc(), RewardRedemption.id.asc())
        .limit(1)
    )
    if redemption is not None:
        redemption.status = "CONSUMED"
        redemption.order_id = order.id
        redemption.consumed_at = now
        applied_rewards.append(
            {
                "redemptionId": redemption.id,
                "grantId": redemption.grant_id,
                "benefit": redemption.benefit_snapshot,
                "status": redemption.status,
            }
        )
    for item, product, unit_price in resolved_items:
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                name_snapshot=product.name,
                quantity=item.quantity,
                unit_price=unit_price,
            )
        )

    claim_plain = secrets.token_urlsafe(32)
    claim = OrderClaim(
        order_id=order.id,
        token_hash=hashlib.sha256(claim_plain.encode()).hexdigest(),
        expires_at=now + timedelta(minutes=10),
    )
    db.add(claim)
    daily_total, grants = evaluate_grants(
        db,
        store_id=store.id,
        customer_key=customer_key,
        business_date=day,
        amount=payload.totalAmount,
    )
    response = success(
        {
            "orderId": order.id,
            "businessDate": day.isoformat(),
            "dailyTotal": daily_total,
            "newRewardGrantIds": [grant.id for grant in grants],
            "appliedRewards": applied_rewards,
            "wifiPass": {
                **pass_data(wifi_pass),
                "breakdown": [wifi_pass.policy_snapshot],
            },
            "orderClaim": {"token": claim_plain, "expiresAt": claim.expires_at.isoformat()},
        }
    )
    if idempotency_key:
        db.add(
            IdempotencyRecord(
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                status_code=201,
                response_json=response,
            )
        )
    publish_event(
        db,
        store_id=store.id,
        event_type="order.created",
        aggregate_type="Order",
        aggregate_id=order.id,
        payload={
            "orderId": order.id,
            "externalOrderId": order.external_order_id,
            "totalAmount": order.total_amount,
            "wifiPassId": wifi_pass.id,
        },
    )
    db.commit()
    return response


@router.post("/pos/orders/{order_id}/refund")
def refund_order(
    order_id: str,
    payload: PosRefundRequest,
    db: Session = Depends(get_db),
    _: None = Depends(require_demo_key),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    order = db.get(Order, order_id)
    if order is None or order.store_id != payload.storeId:
        raise HTTPException(status_code=404, detail="주문을 찾을 수 없습니다.")
    request_hash = _payload_hash(payload)
    scope = f"refund:{order.id}"
    if idempotency_key:
        if not 8 <= len(idempotency_key) <= 200:
            raise HTTPException(status_code=422, detail="Idempotency-Key 길이가 올바르지 않습니다.")
        replay = _existing_idempotent_response(
            db, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        if replay is not None:
            return replay
    refundable = order.total_amount - order.refunded_amount
    refund_amount = payload.refundAmount if payload.refundAmount is not None else refundable
    if refund_amount <= 0 or refund_amount > refundable:
        raise HTTPException(status_code=422, detail="환불 가능 금액을 초과했습니다.")

    previous_refunded_amount = order.refunded_amount
    now = db_now()

    balance = db.scalar(
        select(DailySpendBalance).where(
            DailySpendBalance.store_id == order.store_id,
            DailySpendBalance.business_date == order.business_date,
            DailySpendBalance.customer_key == order.customer_key,
        )
    )
    order.refunded_amount += refund_amount
    order.status = "REFUNDED" if order.refunded_amount == order.total_amount else "PARTIALLY_REFUNDED"
    new_daily_total = balance.total_amount if balance else 0
    if balance is not None:
        balance.total_amount = max(0, balance.total_amount - refund_amount)
        balance.version += 1
        new_daily_total = balance.total_amount

    wifi_pass = db.scalar(
        select(WiFiPass)
        .where(
            WiFiPass.store_id == order.store_id,
            WiFiPass.customer_key == order.customer_key,
            WiFiPass.business_date == order.business_date,
        )
        .with_for_update()
    )
    previous_reclaim = prorated_refunded_wifi_minutes(
        wifi_minutes=order.wifi_minutes,
        total_amount=order.total_amount,
        refunded_amount=previous_refunded_amount,
    )
    target_reclaim = prorated_refunded_wifi_minutes(
        wifi_minutes=order.wifi_minutes,
        total_amount=order.total_amount,
        refunded_amount=order.refunded_amount,
    )
    wifi_minutes_revoked = reclaim_wifi_minutes(
        wifi_pass,
        minutes=target_reclaim - previous_reclaim,
        now=now,
    )

    grants = db.scalars(
        select(RewardGrant).where(
            RewardGrant.store_id == order.store_id,
            RewardGrant.business_date == order.business_date,
            RewardGrant.customer_key == order.customer_key,
            RewardGrant.status.in_(["AWAITING_CHOICE", "FULFILLED"]),
        )
    ).all()
    revoked_grants = []
    for grant in grants:
        tier = db.get(RewardTier, grant.tier_id)
        if tier is not None and tier.threshold_amount > new_daily_total:
            if grant.status == "AWAITING_CHOICE":
                grant.status = "REVOKED"
                revoked_grants.append(grant.id)
            coupon = db.scalar(
                select(Coupon).where(
                    Coupon.grant_id == grant.id,
                    Coupon.status == "AVAILABLE",
                )
            )
            if coupon is not None:
                coupon.status = "REVOKED"

    response = success(
        {
            "orderId": order.id,
            "status": order.status,
            "refundedAmount": order.refunded_amount,
            "refundAmount": refund_amount,
            "dailyTotal": new_daily_total,
            "revokedGrantIds": revoked_grants,
            "wifiMinutesRevoked": wifi_minutes_revoked,
            "wifiPass": pass_data(wifi_pass, now=now) if wifi_pass is not None else None,
        }
    )
    if idempotency_key:
        db.add(
            IdempotencyRecord(
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                status_code=200,
                response_json=response,
            )
        )
    record_audit(
        db,
        store_id=order.store_id,
        action="ORDER_REFUNDED",
        resource_type="Order",
        resource_id=order.id,
        actor_type="POS_CLIENT",
        metadata={
            "refundAmount": refund_amount,
            "wifiMinutesRevoked": wifi_minutes_revoked,
            "reason": payload.reason,
        },
    )
    publish_event(
        db,
        store_id=order.store_id,
        event_type="order.refunded",
        aggregate_type="Order",
        aggregate_id=order.id,
        payload={
            "orderId": order.id,
            "refundAmount": refund_amount,
            "wifiMinutesRevoked": wifi_minutes_revoked,
            "status": order.status,
        },
    )
    db.commit()
    return response
