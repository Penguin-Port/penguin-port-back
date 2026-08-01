import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import issue_token, require_demo_key
from app.db import get_db
from app.http import success
from app.models import Order, OrderClaim, OrderItem, Product, Store, WiFiPass
from app.schemas import PosOrderRequest
from app.services.policy import (
    additional_order_minutes,
    business_date,
    expiry_after,
    first_order_minutes,
)
from app.services.rewards import evaluate_grants
from app.services.wifi import pass_data
from app.time import db_now


router = APIRouter(tags=["POS"])


def _customer_key(payload: PosOrderRequest) -> str:
    if payload.customer.memberId:
        return f"member:{payload.customer.memberId}"
    if payload.customer.phone:
        digits = "".join(character for character in payload.customer.phone if character.isdigit())
        return f"phone:{digits}"
    raise HTTPException(status_code=422, detail="memberId 또는 phone이 필요합니다.")


@router.post("/pos/orders", status_code=201)
def create_order(
    payload: PosOrderRequest,
    db: Session = Depends(get_db),
    _: None = Depends(require_demo_key),
):
    store = db.get(Store, payload.storeId)
    if store is None:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다.")
    duplicate = db.scalar(
        select(Order).where(
            Order.store_id == store.id,
            Order.external_order_id == payload.externalOrderId,
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="이미 처리된 externalOrderId입니다.")

    products = {}
    for item in payload.items:
        product = db.get(Product, item.productId)
        if product is None or product.store_id != store.id or not product.is_active:
            raise HTTPException(status_code=422, detail="주문 상품을 찾을 수 없습니다.")
        products[product.id] = product

    now = db_now()
    customer_key = _customer_key(payload)
    day = business_date(now)
    wifi_pass = db.scalar(
        select(WiFiPass).where(
            WiFiPass.store_id == store.id,
            WiFiPass.customer_key == customer_key,
            WiFiPass.business_date == day,
            WiFiPass.status.not_in(["BLOCKED", "CANCELLED"]),
        )
    )
    if wifi_pass is None:
        minutes, snapshot = first_order_minutes(payload.totalAmount)
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
        minutes, snapshot = additional_order_minutes(payload.totalAmount)
        wifi_pass.expires_at = max(wifi_pass.expires_at, now) + timedelta(minutes=minutes)
        wifi_pass.version += 1
        wifi_pass.policy_snapshot = snapshot
        if wifi_pass.status == "EXPIRED":
            wifi_pass.status = "ACTIVE"
    db.flush()

    order = Order(
        store_id=store.id,
        external_order_id=payload.externalOrderId,
        customer_key=customer_key,
        phone=payload.customer.phone,
        total_amount=payload.totalAmount,
        business_date=day,
        paid_at=payload.paidAt,
    )
    db.add(order)
    db.flush()
    for item in payload.items:
        product = products[item.productId]
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                name_snapshot=product.name,
                quantity=item.quantity,
                unit_price=item.unitPrice if item.unitPrice is not None else product.price,
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
    db.commit()
    return success(
        {
            "orderId": order.id,
            "businessDate": day.isoformat(),
            "dailyTotal": daily_total,
            "newRewardGrantIds": [grant.id for grant in grants],
            "wifiPass": pass_data(wifi_pass),
            "orderClaim": {"token": claim_plain, "expiresAt": claim.expires_at.isoformat()},
        }
    )
