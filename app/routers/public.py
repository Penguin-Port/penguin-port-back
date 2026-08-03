import hashlib
import hmac
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import decode_token, issue_token, require_portal_session
from app.config import settings
from app.db import get_db
from app.http import success
from app.models import (
    Coupon,
    DailySpendBalance,
    DemoMessage,
    Order,
    OrderClaim,
    OrderItem,
    OtpChallenge,
    RewardBenefit,
    RewardGrant,
    RewardTier,
    Store,
    WiFiPass,
)
from app.schemas import (
    ClaimExchangeRequest,
    OtpConfirmRequest,
    OtpSendRequest,
    RewardChooseRequest,
)
from app.services.demo_network import authorize
from app.services.policy import additional_order_minutes, first_order_minutes
from app.services.rewards import choose_benefit
from app.services.wifi import expire_due_passes, pass_data
from app.time import db_now


router = APIRouter(tags=["Public"])


def _hash_code(challenge_id: str, code: str) -> str:
    return hmac.new(
        settings.jwt_secret.encode(),
        f"{challenge_id}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _portal_pass(db: Session, claims: dict, pass_id: str) -> WiFiPass:
    wifi_pass = db.scalar(
        select(WiFiPass).where(
            WiFiPass.id == pass_id,
            WiFiPass.store_id == claims.get("storeId"),
            WiFiPass.customer_key == claims.get("customerKey"),
        )
    )
    if wifi_pass is None:
        raise HTTPException(status_code=404, detail="이용권을 찾을 수 없습니다.")
    return wifi_pass


def _provided_minutes(db: Session, order: Order) -> int:
    first_order_id = db.scalar(
        select(Order.id)
        .where(
            Order.store_id == order.store_id,
            Order.customer_key == order.customer_key,
            Order.business_date == order.business_date,
        )
        .order_by(Order.created_at.asc(), Order.id.asc())
        .limit(1)
    )
    if order.id == first_order_id:
        minutes, _ = first_order_minutes(order.total_amount)
    else:
        minutes, _ = additional_order_minutes(order.total_amount)
    return minutes


@router.post("/public/order-claims/exchange")
def exchange_claim(payload: ClaimExchangeRequest, db: Session = Depends(get_db)):
    token_hash = hashlib.sha256(payload.orderClaim.encode()).hexdigest()
    claim = db.scalar(select(OrderClaim).where(OrderClaim.token_hash == token_hash))
    if claim is None:
        raise HTTPException(status_code=404, detail="주문 Claim이 유효하지 않습니다.")
    now = db_now()
    if claim.exchanged_at is not None:
        raise HTTPException(status_code=409, detail="이미 사용된 주문 Claim입니다.")
    if claim.expires_at <= now:
        raise HTTPException(status_code=410, detail="주문 Claim이 만료되었습니다.")
    order = db.get(Order, claim.order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="주문을 찾을 수 없습니다.")
    store = db.get(Store, order.store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다.")
    order_items = db.scalars(
        select(OrderItem)
        .where(OrderItem.order_id == order.id)
        .order_by(OrderItem.id)
    ).all()
    claim.exchanged_at = now
    ticket = issue_token(
        {
            "kind": "verification_ticket",
            "orderId": order.id,
            "storeId": order.store_id,
            "customerKey": order.customer_key,
        },
        minutes=10,
    )
    wifi_pass = db.scalar(
        select(WiFiPass).where(
            WiFiPass.store_id == order.store_id,
            WiFiPass.customer_key == order.customer_key,
            WiFiPass.business_date == order.business_date,
        )
    )
    db.commit()
    return success(
        {
            "verificationTicket": ticket,
            "requiresVerification": True,
            "passId": wifi_pass.id if wifi_pass else None,
            "expiresIn": 600,
            "storeName": store.name,
            "orderNo": order.external_order_id,
            "items": [
                {
                    "productId": item.product_id,
                    "name": item.name_snapshot,
                    "quantity": item.quantity,
                    "unitPrice": item.unit_price,
                    "lineAmount": item.quantity * item.unit_price,
                }
                for item in order_items
            ],
            "paidAmount": order.total_amount,
            "providedMinutes": _provided_minutes(db, order),
        }
    )


def _verification_claims(ticket: str) -> dict:
    claims = decode_token(ticket)
    if claims.get("kind") != "verification_ticket":
        raise HTTPException(status_code=422, detail="인증 Ticket이 유효하지 않습니다.")
    return claims


@router.post("/public/otp/send", status_code=201)
def send_otp(payload: OtpSendRequest, db: Session = Depends(get_db)):
    claims = _verification_claims(payload.verificationTicket)
    order = db.get(Order, claims.get("orderId"))
    if order is None or order.store_id != claims.get("storeId"):
        raise HTTPException(status_code=404, detail="주문을 찾을 수 없습니다.")
    digits = "".join(character for character in payload.phone if character.isdigit())
    customer_key = f"phone:{digits}"
    if customer_key != order.customer_key:
        raise HTTPException(status_code=422, detail="주문에 연결된 전화번호와 일치하지 않습니다.")
    challenge = OtpChallenge(
        store_id=order.store_id,
        order_id=order.id,
        customer_key=customer_key,
        phone=payload.phone,
        code_hash="pending",
        expires_at=db_now() + timedelta(minutes=3),
    )
    db.add(challenge)
    db.flush()
    code = settings.demo_otp_code
    challenge.code_hash = _hash_code(challenge.id, code)
    db.add(
        DemoMessage(
            store_id=order.store_id,
            channel="SMS",
            destination=payload.phone,
            body=f"인증번호: {code}",
            payload={"challengeId": challenge.id, "demoCode": code},
        )
    )
    db.commit()
    return success(
        {
            "challengeId": challenge.id,
            "expiresAt": challenge.expires_at.isoformat(),
            "maxAttempts": 5,
            "demoCode": code,
        }
    )


@router.post("/public/otp/confirm")
def confirm_otp(payload: OtpConfirmRequest, db: Session = Depends(get_db)):
    challenge = db.get(OtpChallenge, payload.challengeId)
    if challenge is None:
        raise HTTPException(status_code=404, detail="인증 요청을 찾을 수 없습니다.")
    now = db_now()
    if challenge.status != "PENDING":
        raise HTTPException(status_code=422, detail="이미 종료된 인증 요청입니다.")
    if challenge.expires_at <= now:
        challenge.status = "EXPIRED"
        db.commit()
        raise HTTPException(status_code=422, detail="인증번호가 만료되었습니다.")
    if challenge.attempts >= 5:
        challenge.status = "LOCKED"
        db.commit()
        raise HTTPException(status_code=422, detail="인증 시도 횟수를 초과했습니다.")
    challenge.attempts += 1
    if not hmac.compare_digest(challenge.code_hash, _hash_code(challenge.id, payload.code)):
        if challenge.attempts >= 5:
            challenge.status = "LOCKED"
        db.commit()
        raise HTTPException(status_code=422, detail="인증번호가 일치하지 않습니다.")
    challenge.status = "VERIFIED"
    challenge.verified_at = now
    order = db.get(Order, challenge.order_id)
    wifi_pass = db.scalar(
        select(WiFiPass).where(
            WiFiPass.store_id == challenge.store_id,
            WiFiPass.customer_key == challenge.customer_key,
            WiFiPass.business_date == order.business_date,
        )
    )
    portal_session = issue_token(
        {
            "kind": "portal",
            "storeId": challenge.store_id,
            "customerKey": challenge.customer_key,
        },
        minutes=24 * 60,
    )
    db.commit()
    return success(
        {
            "portalSession": portal_session,
            "passId": wifi_pass.id if wifi_pass else None,
            "expiresIn": 86400,
        }
    )


@router.post("/public/passes/{pass_id}/activate")
def activate_pass(
    pass_id: str,
    claims: dict = Depends(require_portal_session),
    db: Session = Depends(get_db),
):
    wifi_pass = _portal_pass(db, claims, pass_id)
    if wifi_pass.status not in ["ISSUED", "ACTIVATING"]:
        raise HTTPException(status_code=409, detail="활성화할 수 없는 이용권 상태입니다.")
    if wifi_pass.expires_at <= db_now():
        wifi_pass.status = "EXPIRED"
        db.commit()
        raise HTTPException(status_code=409, detail="이미 만료된 이용권입니다.")
    wifi_pass.status = "ACTIVE"
    wifi_pass.activated_at = db_now()
    wifi_pass.network_reference = authorize(wifi_pass.id)
    db.commit()
    return success(pass_data(wifi_pass))


@router.get("/public/passes/{pass_id}")
def get_pass(
    pass_id: str,
    claims: dict = Depends(require_portal_session),
    db: Session = Depends(get_db),
):
    wifi_pass = _portal_pass(db, claims, pass_id)
    expire_due_passes(db)
    balance = db.scalar(
        select(DailySpendBalance).where(
            DailySpendBalance.store_id == wifi_pass.store_id,
            DailySpendBalance.customer_key == wifi_pass.customer_key,
            DailySpendBalance.business_date == wifi_pass.business_date,
        )
    )
    data = pass_data(wifi_pass)
    data["dailyTotal"] = balance.total_amount if balance else 0
    return success(data)


@router.get("/public/upsell-hint")
def upsell_hint(
    claims: dict = Depends(require_portal_session),
    db: Session = Depends(get_db),
):
    balance = db.scalar(
        select(DailySpendBalance).where(
            DailySpendBalance.store_id == claims["storeId"],
            DailySpendBalance.customer_key == claims["customerKey"],
        )
    )
    total = balance.total_amount if balance else 0
    tier = db.scalar(
        select(RewardTier)
        .where(
            RewardTier.store_id == claims["storeId"],
            RewardTier.threshold_amount > total,
        )
        .order_by(RewardTier.threshold_amount)
    )
    return success(
        {
            "dailyTotal": total,
            "nextTierAmount": tier.threshold_amount if tier else None,
            "remainingAmountToNextTier": tier.threshold_amount - total if tier else 0,
        }
    )


@router.post("/public/rewards/{grant_id}/choose")
def choose_reward(
    grant_id: str,
    payload: RewardChooseRequest,
    claims: dict = Depends(require_portal_session),
    db: Session = Depends(get_db),
):
    grant = db.get(RewardGrant, grant_id)
    if grant is None or grant.store_id != claims["storeId"] or grant.customer_key != claims["customerKey"]:
        raise HTTPException(status_code=404, detail="리워드 지급 건을 찾을 수 없습니다.")
    benefit = db.get(RewardBenefit, payload.benefitId)
    if benefit is None:
        raise HTTPException(status_code=404, detail="리워드 혜택을 찾을 수 없습니다.")
    try:
        coupon, benefit_data = choose_benefit(
            db, grant=grant, benefit=benefit, fulfill_mode=payload.fulfillMode
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return success(
        {
            "grantId": grant.id,
            "status": grant.status,
            "fulfillMode": grant.fulfill_mode,
            "benefit": benefit_data,
            "coupon": (
                {
                    "couponId": coupon.id,
                    "status": coupon.status,
                    "expiresAt": coupon.expires_at.isoformat(),
                }
                if coupon
                else None
            ),
        }
    )
