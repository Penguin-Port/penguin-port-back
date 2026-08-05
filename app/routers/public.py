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
    RewardRedemption,
    RewardTier,
    Store,
    WiFiPass,
    Product,
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
from app.security import masked_phone, phone_lookup_hash
from app.time import business_date, business_day_end, db_now


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
    if order.wifi_minutes:
        return order.wifi_minutes
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
    try:
        lookup_hash = phone_lookup_hash(payload.phone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    customer_key = f"phone:{lookup_hash}"
    if order.phone_lookup_hash != lookup_hash and customer_key != order.customer_key:
        raise HTTPException(status_code=422, detail="주문에 연결된 전화번호와 일치하지 않습니다.")
    challenge = OtpChallenge(
        store_id=order.store_id,
        order_id=order.id,
        customer_key=customer_key,
        phone=masked_phone(payload.phone),
        phone_lookup_hash=lookup_hash,
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
            destination=masked_phone(payload.phone),
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
@router.get("/public/kiosk/upsell-hint")
def upsell_hint(
    claims: dict = Depends(require_portal_session),
    db: Session = Depends(get_db),
):
    store = db.get(Store, claims["storeId"])
    if store is None:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다.")
    day = business_date(
        db_now(), timezone_name=store.timezone, cutoff=store.business_day_cutoff
    )
    balance = db.scalar(
        select(DailySpendBalance).where(
            DailySpendBalance.store_id == claims["storeId"],
            DailySpendBalance.customer_key == claims["customerKey"],
            DailySpendBalance.business_date == day,
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
    preview = []
    suggested_items = []
    if tier is not None:
        benefits = db.scalars(
            select(RewardBenefit)
            .join(RewardTier, RewardBenefit.tier_id == RewardTier.id)
            .where(RewardTier.id == tier.id)
            .order_by(RewardBenefit.id)
        ).all()
        preview = [benefit.benefit_type for benefit in benefits]
        suggested_items = [
            {
                "productId": product.id,
                "name": product.name,
                "price": product.price,
            }
            for product in db.scalars(
                select(Product)
                .where(Product.store_id == claims["storeId"], Product.is_active.is_(True))
                .order_by(Product.price.asc(), Product.name.asc())
                .limit(3)
            ).all()
        ]
    return success(
        {
            "dailyTotal": total,
            "nextTierAmount": tier.threshold_amount if tier else None,
            "remainingAmountToNextTier": tier.threshold_amount - total if tier else 0,
            "nextTierBenefitsPreview": preview,
            "suggestedItems": suggested_items,
        }
    )


def _portal_grant(db: Session, claims: dict, grant_id: str) -> RewardGrant:
    grant = db.get(RewardGrant, grant_id)
    if (
        grant is None
        or grant.store_id != claims["storeId"]
        or grant.customer_key != claims["customerKey"]
    ):
        raise HTTPException(status_code=404, detail="리워드 지급 건을 찾을 수 없습니다.")
    return grant


@router.get("/public/rewards/grants/{grant_id}/options")
def reward_options(
    grant_id: str,
    claims: dict = Depends(require_portal_session),
    db: Session = Depends(get_db),
):
    grant = _portal_grant(db, claims, grant_id)
    tier = db.get(RewardTier, grant.tier_id)
    if tier is None:
        raise HTTPException(status_code=404, detail="리워드 티어를 찾을 수 없습니다.")
    benefits = db.scalars(
        select(RewardBenefit)
        .where(RewardBenefit.tier_id == grant.tier_id)
        .order_by(RewardBenefit.id)
    ).all()
    return success(
        {
            "grantId": grant.id,
            "tierAmount": tier.threshold_amount,
            "status": grant.status,
            "options": [
                {
                    "benefitId": benefit.id,
                    "type": benefit.benefit_type,
                    "title": benefit.title,
                    "payload": benefit.payload,
                    "recommended": index == 0,
                    "recommendationReason": "현재 MVP 기본 추천 혜택입니다." if index == 0 else None,
                }
                for index, benefit in enumerate(benefits)
            ],
        }
    )


@router.post("/public/rewards/{grant_id}/choose")
@router.post("/public/rewards/grants/{grant_id}/choose")
def choose_reward(
    grant_id: str,
    payload: RewardChooseRequest,
    claims: dict = Depends(require_portal_session),
    db: Session = Depends(get_db),
):
    grant = _portal_grant(db, claims, grant_id)
    benefit = db.get(RewardBenefit, payload.benefitId)
    if benefit is None:
        raise HTTPException(status_code=404, detail="리워드 혜택을 찾을 수 없습니다.")
    current_order = None
    if payload.orderId:
        current_order = db.get(Order, payload.orderId)
        if (
            current_order is None
            or current_order.store_id != grant.store_id
            or current_order.customer_key != grant.customer_key
        ):
            raise HTTPException(status_code=404, detail="현재 주문을 찾을 수 없습니다.")
    try:
        coupon, benefit_data = choose_benefit(
            db, grant=grant, benefit=benefit, fulfill_mode=payload.fulfillMode
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    immediate = None
    if payload.fulfillMode == "IMMEDIATE":
        if benefit.benefit_type == "WIFI_DAY_PASS":
            store = db.get(Store, grant.store_id)
            wifi_pass = db.scalar(
                select(WiFiPass).where(
                    WiFiPass.store_id == grant.store_id,
                    WiFiPass.customer_key == grant.customer_key,
                    WiFiPass.business_date == grant.business_date,
                )
            )
            if store is None or wifi_pass is None:
                raise HTTPException(status_code=422, detail="종일권을 적용할 이용권이 없습니다.")
            if wifi_pass.status in {"BLOCKED", "CANCELLED", "FAILED"}:
                raise HTTPException(status_code=409, detail="현재 이용권에는 종일권을 적용할 수 없습니다.")
            wifi_pass.expires_at = max(
                wifi_pass.expires_at,
                business_day_end(
                    grant.business_date,
                    timezone_name=store.timezone,
                    cutoff=store.business_day_cutoff,
                ),
            )
            wifi_pass.version += 1
            immediate = {"type": "WIFI_DAY_PASS", "wifiPass": pass_data(wifi_pass)}
        redemption_status = "CONSUMED" if current_order is not None else "AVAILABLE"
        redemption = RewardRedemption(
            store_id=grant.store_id,
            grant_id=grant.id,
            benefit_id=benefit.id,
            customer_key=grant.customer_key,
            business_date=grant.business_date,
            status=redemption_status,
            order_id=current_order.id if current_order is not None else None,
            benefit_snapshot=benefit_data,
            consumed_at=db_now() if current_order is not None else None,
        )
        db.add(redemption)
        db.flush()
        immediate = {
            **(immediate or {}),
            "redemptionId": redemption.id,
            "status": redemption.status,
            "orderId": redemption.order_id,
        }
    db.commit()
    return success(
        {
            "grantId": grant.id,
            "status": grant.status,
            "fulfillMode": grant.fulfill_mode,
            "benefit": benefit_data,
            "immediate": immediate,
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


@router.get("/public/coupons")
def list_coupons(
    claims: dict = Depends(require_portal_session),
    db: Session = Depends(get_db),
):
    coupons = db.scalars(
        select(Coupon)
        .where(
            Coupon.store_id == claims["storeId"],
            Coupon.customer_key == claims["customerKey"],
        )
        .order_by(Coupon.created_at.desc(), Coupon.id.desc())
    ).all()
    now = db_now()
    changed = False
    for coupon in coupons:
        if coupon.status == "AVAILABLE" and coupon.expires_at <= now:
            coupon.status = "EXPIRED"
            changed = True
    if changed:
        db.commit()
    return success(
        [
            {
                "couponId": coupon.id,
                "status": coupon.status,
                "benefit": coupon.benefit_snapshot,
                "expiresAt": coupon.expires_at.isoformat(),
                "redeemedAt": coupon.redeemed_at.isoformat() if coupon.redeemed_at else None,
            }
            for coupon in coupons
        ]
    )


@router.get("/public/stores/{store_id}/privacy-notice")
def privacy_notice(store_id: str, db: Session = Depends(get_db)):
    store = db.get(Store, store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다.")
    return success(
        {
            "storeId": store.id,
            "storeName": store.name,
            "phoneStorage": "전화번호는 원문 대신 lookup hash와 last4로 연결됩니다.",
            "phoneRetentionDays": settings.phone_retention_days,
            "automaticDeletion": True,
            "purpose": "Wi-Fi 이용권 연결, OTP 인증, 점주 보호를 위한 최소 정보 처리",
            "supportNote": "불법 접속·분쟁 대응에 필요한 감사 정보는 별도 보존 기간을 적용합니다.",
        }
    )


@router.post("/public/coupons/{coupon_id}/redeem")
def redeem_coupon(
    coupon_id: str,
    claims: dict = Depends(require_portal_session),
    db: Session = Depends(get_db),
):
    coupon = db.scalar(
        select(Coupon).where(
            Coupon.id == coupon_id,
            Coupon.store_id == claims["storeId"],
            Coupon.customer_key == claims["customerKey"],
        )
    )
    if coupon is None:
        raise HTTPException(status_code=404, detail="쿠폰을 찾을 수 없습니다.")
    if coupon.status != "AVAILABLE":
        raise HTTPException(status_code=409, detail="사용할 수 없는 쿠폰 상태입니다.")
    now = db_now()
    if coupon.expires_at <= now:
        coupon.status = "EXPIRED"
        db.commit()
        raise HTTPException(status_code=410, detail="쿠폰이 만료되었습니다.")
    coupon.status = "REDEEMED"
    coupon.redeemed_at = now
    db.commit()
    return success(
        {
            "couponId": coupon.id,
            "status": coupon.status,
            "redeemedAt": coupon.redeemed_at.isoformat(),
            "benefit": coupon.benefit_snapshot,
        }
    )
