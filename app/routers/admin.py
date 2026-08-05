from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import decode_token, hash_token, issue_token, require_admin, verify_password
from app.config import settings
from app.db import get_db
from app.http import success
from app.models import (
    AdminUser,
    AIRecommendation,
    AuditLog,
    InventoryItem,
    InventoryEvent,
    Product,
    Promotion,
    RefreshTokenSession,
    RewardBenefit,
    RewardTier,
    Store,
    WiFiPass,
)
from app.schemas import (
    AdminLoginRequest,
    AdminLogoutRequest,
    AdminPassExpireRequest,
    AdminPassExtendRequest,
    AdminRefreshRequest,
    RecommendationDecisionRequest,
    RecommendationPatchRequest,
    RecommendationRejectRequest,
    WifiPolicyPublishRequest,
    WifiPolicySimulationRequest,
    InventoryAdjustRequest,
    InventoryUpsertRequest,
    RewardTierUpsertRequest,
)
from app.services.analytics import (
    get_or_create_sales_recommendation,
    sales_summary as build_sales_summary,
)
from app.services.audit import record_audit
from app.services.demo_network import revoke
from app.services.inventory import calculate_risk_score, inventory_data, scan_inventory
from app.services.policy import additional_order_minutes, first_order_minutes
from app.services.wifi import expire_due_passes, pass_data
from app.time import aware, business_date as current_business_date, db_now


router = APIRouter(tags=["Admin"])

REFRESH_COOKIE = "smartpass_refresh"
WRITE_ROLES = {"OWNER", "MANAGER"}


def _admin_user(db: Session, claims: dict) -> AdminUser:
    user = db.get(AdminUser, claims.get("adminId"))
    if user is None:
        raise HTTPException(status_code=401, detail="관리자를 찾을 수 없습니다.")
    return user


def _require_write_role(db: Session, claims: dict) -> AdminUser:
    user = _admin_user(db, claims)
    if user.role not in WRITE_ROLES:
        raise HTTPException(status_code=403, detail="이 작업을 수행할 관리자 권한이 없습니다.")
    return user


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=settings.refresh_token_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/admin",
    )


def _new_refresh_session(db: Session, user: AdminUser) -> tuple[str, RefreshTokenSession]:
    session = RefreshTokenSession(
        admin_id=user.id,
        token_hash="pending",
        expires_at=db_now() + timedelta(days=settings.refresh_token_days),
    )
    db.add(session)
    db.flush()
    token = issue_token(
        {
            "kind": "admin_refresh",
            "adminId": user.id,
            "storeId": user.store_id,
            "sessionId": session.id,
        },
        minutes=settings.refresh_token_days * 24 * 60,
    )
    session.token_hash = hash_token(token)
    return token, session


def _access_payload(user: AdminUser) -> dict:
    access_token = issue_token(
        {"kind": "admin", "adminId": user.id, "storeId": user.store_id, "role": user.role},
        minutes=settings.access_token_minutes,
    )
    return {
        "adminId": user.id,
        "storeId": user.store_id,
        "username": user.username,
        "role": user.role,
        "accessToken": access_token,
        "accessExpiresIn": settings.access_token_minutes * 60,
    }


@router.post("/admin/login")
def login(
    payload: AdminLoginRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    user = db.scalar(select(AdminUser).where(AdminUser.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    refresh_token, _ = _new_refresh_session(db, user)
    data = _access_payload(user)
    data["refreshToken"] = refresh_token
    data["refreshExpiresIn"] = settings.refresh_token_days * 24 * 60 * 60
    _set_refresh_cookie(response, refresh_token)
    db.commit()
    return success(data)


@router.post("/admin/refresh")
def refresh(
    payload: AdminRefreshRequest,
    response: Response,
    refresh_cookie: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    db: Session = Depends(get_db),
):
    raw_token = payload.refreshToken or refresh_cookie
    if not raw_token:
        raise HTTPException(status_code=401, detail="Refresh Token이 필요합니다.")
    claims = decode_token(raw_token)
    if claims.get("kind") != "admin_refresh":
        raise HTTPException(status_code=401, detail="Refresh Token이 아닙니다.")
    session = db.get(RefreshTokenSession, claims.get("sessionId"))
    if (
        session is None
        or session.token_hash != hash_token(raw_token)
        or session.revoked_at is not None
        or session.expires_at <= db_now()
    ):
        raise HTTPException(status_code=401, detail="Refresh Token이 만료되었거나 폐기되었습니다.")
    user = db.get(AdminUser, session.admin_id)
    if user is None:
        raise HTTPException(status_code=401, detail="관리자를 찾을 수 없습니다.")
    session.revoked_at = db_now()
    new_refresh, new_session = _new_refresh_session(db, user)
    session.replaced_by = new_session.id
    data = _access_payload(user)
    data["refreshToken"] = new_refresh
    data["refreshExpiresIn"] = settings.refresh_token_days * 24 * 60 * 60
    _set_refresh_cookie(response, new_refresh)
    db.commit()
    return success(data)


@router.post("/admin/logout")
def logout(
    payload: AdminLogoutRequest,
    response: Response,
    refresh_cookie: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    db: Session = Depends(get_db),
):
    raw_token = payload.refreshToken or refresh_cookie
    if raw_token:
        try:
            claims = decode_token(raw_token)
        except HTTPException:
            claims = {}
        session = db.get(RefreshTokenSession, claims.get("sessionId"))
        if session is not None and session.token_hash == hash_token(raw_token):
            session.revoked_at = db_now()
            db.commit()
    response.delete_cookie(REFRESH_COOKIE, path="/admin")
    return success({"loggedOut": True})


@router.get("/admin/me")
def me(
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = _admin_user(db, claims)
    return success(
        {
            "adminId": user.id,
            "storeId": user.store_id,
            "username": user.username,
            "role": user.role,
        }
    )


def _store_for_admin(db: Session, claims: dict) -> Store:
    store = db.get(Store, claims.get("storeId"))
    if store is None:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다.")
    return store


def _policy_data(store: Store) -> dict:
    config = store.policy_config or {}
    return {
        "storeId": store.id,
        "version": store.policy_version,
        "baseMinutes": config.get("baseMinutes", 120),
        "firstOrderTiers": config.get(
            "firstOrderTiers",
            [
                {"minAmount": 10000, "minutes": 30},
                {"minAmount": 15000, "minutes": 60},
            ],
        ),
        "additionalOrderTiers": config.get(
            "additionalOrderTiers",
            [
                {"minAmount": 5000, "minutes": 60},
                {"minAmount": 10000, "minutes": 120},
            ],
        ),
    }


def _validate_policy(payload: WifiPolicyPublishRequest) -> dict:
    def tiers(items, label):
        values = [{"minAmount": item.minAmount, "minutes": item.minutes} for item in items]
        if any(left["minAmount"] >= right["minAmount"] for left, right in zip(values, values[1:])):
            raise HTTPException(status_code=422, detail=f"{label} 금액 구간은 오름차순이어야 합니다.")
        return values

    return {
        "baseMinutes": payload.baseMinutes,
        "firstOrderTiers": tiers(payload.firstOrderTiers, "첫 주문"),
        "additionalOrderTiers": tiers(payload.additionalOrderTiers, "추가 주문"),
    }


@router.get("/admin/wifi/policies")
def get_wifi_policy(
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return success(_policy_data(_store_for_admin(db, claims)))


@router.post("/admin/wifi/policies/simulate")
def simulate_wifi_policy(
    payload: WifiPolicySimulationRequest,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    store = _store_for_admin(db, claims)
    policy = _policy_data(store)
    policy.pop("storeId", None)
    policy.pop("version", None)
    if payload.orderType == "FIRST":
        minutes, breakdown = first_order_minutes(payload.amount, policy)
    else:
        minutes, breakdown = additional_order_minutes(payload.amount, policy)
    return success({"minutes": minutes, "breakdown": breakdown, "policyVersion": store.policy_version})


@router.post("/admin/wifi/policies/publish")
def publish_wifi_policy(
    payload: WifiPolicyPublishRequest,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _require_write_role(db, claims)
    store = _store_for_admin(db, claims)
    if payload.version != store.policy_version:
        raise HTTPException(status_code=409, detail="Wi-Fi 정책 버전이 충돌했습니다.")
    store.policy_config = _validate_policy(payload)
    store.policy_version += 1
    record_audit(
        db,
        store_id=store.id,
        action="WIFI_POLICY_PUBLISHED",
        resource_type="Store",
        resource_id=store.id,
        actor_type="ADMIN",
        actor_id=claims.get("adminId"),
        metadata={"version": store.policy_version},
    )
    db.commit()
    return success(_policy_data(store))


def _reward_tier_data(db: Session, tier: RewardTier) -> dict:
    benefits = db.scalars(
        select(RewardBenefit)
        .where(RewardBenefit.tier_id == tier.id)
        .order_by(RewardBenefit.id)
    ).all()
    return {
        "tierId": tier.id,
        "name": tier.name,
        "thresholdAmount": tier.threshold_amount,
        "sortOrder": tier.sort_order,
        "benefits": [
            {
                "benefitId": benefit.id,
                "benefitType": benefit.benefit_type,
                "title": benefit.title,
                "payload": benefit.payload,
            }
            for benefit in benefits
        ],
    }


@router.get("/admin/rewards/tiers")
def list_reward_tiers(
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    tiers = db.scalars(
        select(RewardTier)
        .where(RewardTier.store_id == claims["storeId"])
        .order_by(RewardTier.sort_order, RewardTier.threshold_amount)
    ).all()
    return success([_reward_tier_data(db, tier) for tier in tiers])


@router.post("/admin/rewards/tiers")
def upsert_reward_tier(
    payload: RewardTierUpsertRequest,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _require_write_role(db, claims)
    if payload.storeId != claims["storeId"]:
        raise HTTPException(status_code=403, detail="해당 매장에 접근할 권한이 없습니다.")
    tier = db.get(RewardTier, payload.tierId) if payload.tierId else None
    if tier is not None and tier.store_id != claims["storeId"]:
        raise HTTPException(status_code=404, detail="리워드 티어를 찾을 수 없습니다.")
    conflict = db.scalar(
        select(RewardTier).where(
            RewardTier.store_id == claims["storeId"],
            RewardTier.threshold_amount == payload.thresholdAmount,
        )
    )
    if conflict is not None and (tier is None or conflict.id != tier.id):
        raise HTTPException(status_code=409, detail="같은 매장에 동일한 리워드 금액 구간이 있습니다.")
    if tier is None:
        tier = RewardTier(
            store_id=claims["storeId"],
            name=payload.name,
            threshold_amount=payload.thresholdAmount,
            sort_order=payload.sortOrder,
        )
        db.add(tier)
        db.flush()
    else:
        tier.name = payload.name
        tier.threshold_amount = payload.thresholdAmount
        tier.sort_order = payload.sortOrder
    for item in payload.benefits:
        benefit = db.get(RewardBenefit, item.benefitId) if item.benefitId else None
        if benefit is not None and benefit.tier_id != tier.id:
            raise HTTPException(status_code=404, detail="리워드 혜택을 찾을 수 없습니다.")
        if benefit is None:
            benefit = RewardBenefit(tier_id=tier.id)
            db.add(benefit)
        benefit.benefit_type = item.benefitType
        benefit.title = item.title
        benefit.payload = item.payload
    record_audit(
        db,
        store_id=tier.store_id,
        action="REWARD_TIER_UPDATED",
        resource_type="RewardTier",
        resource_id=tier.id,
        actor_type="ADMIN",
        actor_id=claims.get("adminId"),
        metadata={"thresholdAmount": tier.threshold_amount},
    )
    db.commit()
    return success(_reward_tier_data(db, tier))


@router.get("/admin/audit")
def list_audit_logs(
    limit: int = Query(default=100, ge=1, le=200),
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    logs = db.scalars(
        select(AuditLog)
        .where(AuditLog.store_id == claims["storeId"])
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    ).all()
    return success(
        [
            {
                "auditId": log.id,
                "action": log.action,
                "resourceType": log.resource_type,
                "resourceId": log.resource_id,
                "actorType": log.actor_type,
                "actorId": log.actor_id,
                "metadata": log.metadata_json,
                "createdAt": aware(log.created_at).isoformat() if log.created_at else None,
            }
            for log in logs
        ]
    )


@router.get("/admin/passes/active")
def active_passes(
    store_id: str | None = Query(default=None, alias="storeId"),
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    store_id = store_id or claims["storeId"]
    if store_id != claims["storeId"]:
        raise HTTPException(status_code=403, detail="해당 매장에 접근할 권한이 없습니다.")
    expire_due_passes(db)
    passes = db.scalars(
        select(WiFiPass)
        .where(
            WiFiPass.store_id == store_id,
            WiFiPass.status.in_(["ACTIVE", "EXPIRING_SOON"]),
        )
        .order_by(WiFiPass.issued_at.desc())
    ).all()
    return success([pass_data(item) for item in passes])


@router.get("/admin/ai/sales-summary")
def sales_summary(
    business_date: date | None = Query(default=None, alias="businessDate"),
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    store = _store_for_admin(db, claims)
    target_date = business_date or current_business_date(
        db_now(), timezone_name=store.timezone, cutoff=store.business_day_cutoff
    )
    summary = build_sales_summary(db, store=store, business_date=target_date)
    recommendation = get_or_create_sales_recommendation(db, store=store, summary=summary)
    db.commit()
    return success(
        {
            **summary,
            "recommendation": {
                "recommendationId": recommendation.id,
                "summary": recommendation.payload.get("summary"),
                "reason": recommendation.reason,
                "evidence": recommendation.evidence,
                "confidence": recommendation.confidence,
            },
        }
    )


@router.get("/admin/inventory")
def list_inventory(
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    items = db.scalars(
        select(InventoryItem)
        .where(InventoryItem.store_id == claims["storeId"])
        .order_by(InventoryItem.risk_score.desc(), InventoryItem.updated_at.desc())
    ).all()
    return success([inventory_data(db, item) for item in items])


@router.post("/admin/inventory")
def upsert_inventory(
    payload: InventoryUpsertRequest,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _require_write_role(db, claims)
    if payload.storeId != claims["storeId"]:
        raise HTTPException(status_code=403, detail="해당 매장에 접근할 권한이 없습니다.")
    product = db.get(Product, payload.productId)
    if product is None or product.store_id != claims["storeId"]:
        raise HTTPException(status_code=404, detail="상품을 찾을 수 없습니다.")
    item = db.scalar(
        select(InventoryItem).where(
            InventoryItem.store_id == claims["storeId"],
            InventoryItem.product_id == product.id,
        )
    )
    if item is None:
        item = InventoryItem(store_id=claims["storeId"], product_id=product.id)
        db.add(item)
    item.quantity = payload.quantity
    item.low_stock_threshold = payload.lowStockThreshold
    item.expires_on = payload.expiresOn
    item.unit = payload.unit
    item.risk_score = calculate_risk_score(item)
    item.updated_at = db_now()
    db.commit()
    return success(inventory_data(db, item))


@router.post("/admin/inventory/{item_id}/adjust")
def adjust_inventory(
    item_id: str,
    payload: InventoryAdjustRequest,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _require_write_role(db, claims)
    if payload.storeId != claims["storeId"]:
        raise HTTPException(status_code=403, detail="해당 매장에 접근할 권한이 없습니다.")
    item = db.scalar(
        select(InventoryItem).where(
            InventoryItem.id == item_id,
            InventoryItem.store_id == claims["storeId"],
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="재고 항목을 찾을 수 없습니다.")
    new_quantity = item.quantity + payload.quantityDelta
    if new_quantity < 0:
        raise HTTPException(status_code=422, detail="재고 수량은 0보다 작을 수 없습니다.")
    item.quantity = new_quantity
    item.risk_score = calculate_risk_score(item)
    item.updated_at = db_now()
    db.add(
        InventoryEvent(
            item_id=item.id,
            type="ADJUSTED",
            quantity_delta=payload.quantityDelta,
            reason=payload.reason,
        )
    )
    db.commit()
    return success(inventory_data(db, item))


@router.post("/admin/inventory/scan")
def scan_inventory_risk(
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _require_write_role(db, claims)
    recommendations = scan_inventory(db, store_id=claims["storeId"])
    db.commit()
    return success(
        [
            {
                "recommendationId": item.id,
                "type": item.type,
                "payload": item.payload,
                "reason": item.reason,
                "evidence": item.evidence,
                "confidence": item.confidence,
                "status": item.status,
                "version": item.version,
            }
            for item in recommendations
        ]
    )


@router.get("/admin/ai/inventory")
def inventory_recommendations(
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    items = db.scalars(
        select(AIRecommendation)
        .where(
            AIRecommendation.store_id == claims["storeId"],
            AIRecommendation.type == "INVENTORY_PROMOTION",
        )
        .order_by(AIRecommendation.created_at.desc())
    ).all()
    return success(
        [
            {
                "recommendationId": item.id,
                "payload": item.payload,
                "reason": item.reason,
                "evidence": item.evidence,
                "confidence": item.confidence,
                "status": item.status,
                "version": item.version,
            }
            for item in items
        ]
    )


@router.get("/admin/ai/menu-trends")
def menu_trends(
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _store_for_admin(db, claims)
    return success(
        [
            {
                "menuName": name,
                "reason": "외부 트렌드 연동 전 규칙 기반 폴백 카드입니다.",
                "source": "FALLBACK_TEMPLATE",
            }
            for name in ["말차 디저트", "버터떡", "시즌 과일 라떼"]
        ]
    )


@router.post("/admin/passes/{pass_id}/extend")
def extend_pass(
    pass_id: str,
    payload: AdminPassExtendRequest,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _require_write_role(db, claims)
    wifi_pass = db.get(WiFiPass, pass_id)
    if wifi_pass is None:
        raise HTTPException(status_code=404, detail="이용권을 찾을 수 없습니다.")
    if wifi_pass.store_id != claims["storeId"] or (
        payload.storeId and payload.storeId != wifi_pass.store_id
    ):
        raise HTTPException(status_code=403, detail="해당 매장에 접근할 권한이 없습니다.")
    wifi_pass.expires_at = max(wifi_pass.expires_at, db_now()) + timedelta(
        minutes=payload.minutes
    )
    wifi_pass.version += 1
    if wifi_pass.status == "EXPIRED":
        wifi_pass.status = "ACTIVE"
    record_audit(
        db,
        store_id=wifi_pass.store_id,
        action="WIFI_PASS_EXTENDED",
        resource_type="WiFiPass",
        resource_id=wifi_pass.id,
        actor_type="ADMIN",
        actor_id=claims.get("adminId"),
        metadata={"minutes": payload.minutes, "version": wifi_pass.version},
    )
    db.commit()
    return success(pass_data(wifi_pass))


@router.post("/admin/passes/{pass_id}/expire")
def expire_pass(
    pass_id: str,
    payload: AdminPassExpireRequest | None = None,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _require_write_role(db, claims)
    wifi_pass = db.get(WiFiPass, pass_id)
    if wifi_pass is None:
        raise HTTPException(status_code=404, detail="이용권을 찾을 수 없습니다.")
    requested_store = payload.storeId if payload else None
    if wifi_pass.store_id != claims["storeId"] or (
        requested_store and requested_store != wifi_pass.store_id
    ):
        raise HTTPException(status_code=403, detail="해당 매장에 접근할 권한이 없습니다.")
    if wifi_pass.status not in ["EXPIRED", "BLOCKED", "CANCELLED"]:
        revoke(wifi_pass.network_reference or "")
        wifi_pass.status = "EXPIRED"
        wifi_pass.network_reference = None
        wifi_pass.version += 1
        record_audit(
            db,
            store_id=wifi_pass.store_id,
            action="WIFI_PASS_EXPIRED",
            resource_type="WiFiPass",
            resource_id=wifi_pass.id,
            actor_type="ADMIN",
            actor_id=claims.get("adminId"),
            metadata={"version": wifi_pass.version},
        )
        db.commit()
    return success(pass_data(wifi_pass))


@router.get("/admin/ai/recommendations")
def recommendations(
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    items = db.scalars(
        select(AIRecommendation)
        .where(AIRecommendation.store_id == claims["storeId"])
        .order_by(AIRecommendation.created_at.desc())
    ).all()
    return success(
        [
            {
                "recommendationId": item.id,
                "type": item.type,
                "payload": item.payload,
                "reason": item.reason,
                "status": item.status,
                "version": item.version,
                "createdAt": aware(item.created_at).isoformat() if item.created_at else None,
                "decidedAt": aware(item.decided_at).isoformat() if item.decided_at else None,
            }
            for item in items
        ]
    )


def _promotion_payload(
    db: Session,
    recommendation: AIRecommendation,
    overrides: RecommendationDecisionRequest,
) -> tuple[dict, datetime, datetime]:
    updated = dict(recommendation.payload or {})
    if overrides.menuIds is not None:
        if not overrides.menuIds:
            raise HTTPException(status_code=422, detail="프로모션 메뉴를 하나 이상 선택해야 합니다.")
        products = db.scalars(
            select(Product).where(
                Product.store_id == recommendation.store_id,
                Product.id.in_(overrides.menuIds),
                Product.is_active.is_(True),
            )
        ).all()
        if len(products) != len(set(overrides.menuIds)):
            raise HTTPException(status_code=422, detail="프로모션 메뉴가 해당 매장에 없습니다.")
        updated["menuIds"] = overrides.menuIds

    discount_rate = (
        overrides.discountRate
        if overrides.discountRate is not None
        else updated.get("discountRate")
    )
    if discount_rate is None or not 0 <= int(discount_rate) <= settings.promotion_max_discount_rate:
        raise HTTPException(
            status_code=422,
            detail=f"할인율은 0~{settings.promotion_max_discount_rate}% 범위여야 합니다.",
        )
    updated["discountRate"] = int(discount_rate)

    try:
        starts_at = overrides.startsAt or datetime.fromisoformat(updated["startsAt"])
        ends_at = overrides.endsAt or datetime.fromisoformat(updated["endsAt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="추천의 프로모션 시간이 유효하지 않습니다.") from exc
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=timezone.utc)
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)
    starts_at = starts_at.astimezone(timezone.utc)
    ends_at = ends_at.astimezone(timezone.utc)
    if ends_at <= starts_at:
        raise HTTPException(status_code=422, detail="프로모션 종료 시각은 시작 시각보다 늦어야 합니다.")
    if ends_at - starts_at > timedelta(hours=settings.promotion_max_duration_hours):
        raise HTTPException(status_code=422, detail="프로모션 적용 시간 범위를 초과했습니다.")
    updated["startsAt"] = starts_at.isoformat()
    updated["endsAt"] = ends_at.isoformat()
    if not updated.get("title"):
        raise HTTPException(status_code=422, detail="프로모션 제목이 필요합니다.")
    return updated, starts_at, ends_at


@router.patch("/admin/ai/recommendations/{recommendation_id}")
def edit_recommendation(
    recommendation_id: str,
    payload: RecommendationPatchRequest,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _require_write_role(db, claims)
    if payload.storeId != claims["storeId"]:
        raise HTTPException(status_code=403, detail="해당 매장에 접근할 권한이 없습니다.")
    recommendation = db.get(AIRecommendation, recommendation_id)
    if recommendation is None or recommendation.store_id != claims["storeId"]:
        raise HTTPException(status_code=404, detail="AI 추천을 찾을 수 없습니다.")
    if recommendation.status not in {"PENDING", "EDITED"} or recommendation.version != payload.version:
        raise HTTPException(status_code=409, detail="추천 버전 또는 상태가 충돌했습니다.")
    updated, _, _ = _promotion_payload(db, recommendation, payload)
    recommendation.payload = updated
    recommendation.status = "EDITED"
    recommendation.version += 1
    record_audit(
        db,
        store_id=recommendation.store_id,
        action="AI_RECOMMENDATION_EDITED",
        resource_type="AIRecommendation",
        resource_id=recommendation.id,
        actor_type="ADMIN",
        actor_id=claims.get("adminId"),
        metadata={"version": recommendation.version},
    )
    db.commit()
    return success(
        {
            "recommendationId": recommendation.id,
            "payload": recommendation.payload,
            "status": recommendation.status,
            "version": recommendation.version,
        }
    )


@router.post("/admin/ai/recommendations/{recommendation_id}/accept", status_code=201)
def accept_recommendation(
    recommendation_id: str,
    payload: RecommendationDecisionRequest,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _require_write_role(db, claims)
    if payload.storeId != claims["storeId"]:
        raise HTTPException(status_code=403, detail="해당 매장에 접근할 권한이 없습니다.")
    recommendation = db.get(AIRecommendation, recommendation_id)
    if recommendation is None or recommendation.store_id != claims["storeId"]:
        raise HTTPException(status_code=404, detail="AI 추천을 찾을 수 없습니다.")
    if recommendation.status not in {"PENDING", "EDITED"} or recommendation.version != payload.version:
        raise HTTPException(status_code=409, detail="추천 버전 또는 상태가 충돌했습니다.")
    updated, starts_at, ends_at = _promotion_payload(db, recommendation, payload)
    now = datetime.now(timezone.utc)
    promotion = Promotion(
        store_id=recommendation.store_id,
        recommendation_id=recommendation.id,
        title=updated["title"],
        payload=updated,
        starts_at=starts_at,
        ends_at=ends_at,
        status="ACTIVE" if starts_at <= now < ends_at else "SCHEDULED",
    )
    recommendation.payload = updated
    recommendation.status = "ACCEPTED"
    recommendation.version += 1
    recommendation.decided_at = db_now()
    db.add(promotion)
    record_audit(
        db,
        store_id=recommendation.store_id,
        action="AI_RECOMMENDATION_ACCEPTED",
        resource_type="AIRecommendation",
        resource_id=recommendation.id,
        actor_type="ADMIN",
        actor_id=claims.get("adminId"),
        metadata={"promotionTitle": promotion.title},
    )
    db.commit()
    return success(
        {
            "recommendationId": recommendation.id,
            "promotionId": promotion.id,
            "title": promotion.title,
            "startsAt": promotion.starts_at.isoformat(),
            "endsAt": promotion.ends_at.isoformat(),
            "status": recommendation.status,
            "version": recommendation.version,
        }
    )


@router.post("/admin/ai/recommendations/{recommendation_id}/reject")
def reject_recommendation(
    recommendation_id: str,
    payload: RecommendationRejectRequest | None = None,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    recommendation = db.get(AIRecommendation, recommendation_id)
    if recommendation is None or recommendation.store_id != claims["storeId"]:
        raise HTTPException(status_code=404, detail="AI 추천을 찾을 수 없습니다.")
    if payload and payload.storeId and payload.storeId != claims["storeId"]:
        raise HTTPException(status_code=403, detail="해당 매장에 접근할 권한이 없습니다.")
    if recommendation.status not in {"PENDING", "EDITED"}:
        raise HTTPException(status_code=409, detail="대기 중인 추천만 거절할 수 있습니다.")
    recommendation.status = "REJECTED"
    recommendation.version += 1
    recommendation.decided_at = db_now()
    if payload and payload.reason:
        recommendation.payload = {**recommendation.payload, "rejectionReason": payload.reason}
    record_audit(
        db,
        store_id=recommendation.store_id,
        action="AI_RECOMMENDATION_REJECTED",
        resource_type="AIRecommendation",
        resource_id=recommendation.id,
        actor_type="ADMIN",
        actor_id=claims.get("adminId"),
        metadata={"reasonProvided": bool(payload and payload.reason)},
    )
    db.commit()
    return success(
        {
            "recommendationId": recommendation.id,
            "status": recommendation.status,
            "version": recommendation.version,
        }
    )
