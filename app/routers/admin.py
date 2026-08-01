from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import issue_token, require_admin, verify_password
from app.db import get_db
from app.http import success
from app.models import AdminUser, AIRecommendation, Promotion, WiFiPass
from app.schemas import (
    AdminLoginRequest,
    AdminPassExpireRequest,
    AdminPassExtendRequest,
    RecommendationDecisionRequest,
    RecommendationRejectRequest,
)
from app.services.demo_network import revoke
from app.services.wifi import expire_due_passes, pass_data
from app.time import db_now


router = APIRouter(tags=["Admin"])


def _admin_user(db: Session, claims: dict) -> AdminUser:
    user = db.get(AdminUser, claims.get("adminId"))
    if user is None:
        raise HTTPException(status_code=401, detail="관리자를 찾을 수 없습니다.")
    return user


@router.post("/admin/login")
def login(payload: AdminLoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(AdminUser).where(AdminUser.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    token = issue_token(
        {"kind": "admin", "adminId": user.id, "storeId": user.store_id},
        minutes=60,
    )
    return success(
        {
            "adminId": user.id,
            "storeId": user.store_id,
            "username": user.username,
            "accessToken": token,
            "accessExpiresIn": 3600,
        }
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


@router.post("/admin/passes/{pass_id}/extend")
def extend_pass(
    pass_id: str,
    payload: AdminPassExtendRequest,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
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
    db.commit()
    return success(pass_data(wifi_pass))


@router.post("/admin/passes/{pass_id}/expire")
def expire_pass(
    pass_id: str,
    payload: AdminPassExpireRequest | None = None,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
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
            }
            for item in items
        ]
    )


@router.post("/admin/ai/recommendations/{recommendation_id}/accept", status_code=201)
def accept_recommendation(
    recommendation_id: str,
    payload: RecommendationDecisionRequest,
    claims: dict = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.storeId != claims["storeId"]:
        raise HTTPException(status_code=403, detail="해당 매장에 접근할 권한이 없습니다.")
    recommendation = db.get(AIRecommendation, recommendation_id)
    if recommendation is None or recommendation.store_id != claims["storeId"]:
        raise HTTPException(status_code=404, detail="AI 추천을 찾을 수 없습니다.")
    if recommendation.status != "PENDING" or recommendation.version != payload.version:
        raise HTTPException(status_code=409, detail="추천 버전 또는 상태가 충돌했습니다.")
    starts_at = datetime.fromisoformat(recommendation.payload["startsAt"])
    ends_at = datetime.fromisoformat(recommendation.payload["endsAt"])
    promotion = Promotion(
        store_id=recommendation.store_id,
        recommendation_id=recommendation.id,
        title=recommendation.payload["title"],
        payload=recommendation.payload,
        starts_at=starts_at,
        ends_at=ends_at,
    )
    recommendation.status = "ACCEPTED"
    recommendation.version += 1
    recommendation.decided_at = db_now()
    db.add(promotion)
    db.commit()
    return success(
        {
            "promotionId": promotion.id,
            "title": promotion.title,
            "startsAt": promotion.starts_at.isoformat(),
            "endsAt": promotion.ends_at.isoformat(),
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
    if recommendation.status != "PENDING":
        raise HTTPException(status_code=409, detail="대기 중인 추천만 거절할 수 있습니다.")
    recommendation.status = "REJECTED"
    recommendation.version += 1
    recommendation.decided_at = db_now()
    if payload and payload.reason:
        recommendation.payload = {**recommendation.payload, "rejectionReason": payload.reason}
    db.commit()
    return success(
        {
            "recommendationId": recommendation.id,
            "status": recommendation.status,
            "version": recommendation.version,
        }
    )
