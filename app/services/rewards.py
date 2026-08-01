from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Coupon, DailySpendBalance, RewardBenefit, RewardGrant, RewardTier
from app.time import db_now


def evaluate_grants(
    db: Session, *, store_id: str, customer_key: str, business_date, amount: int
) -> tuple[int, list[RewardGrant]]:
    balance = db.scalar(
        select(DailySpendBalance).where(
            DailySpendBalance.store_id == store_id,
            DailySpendBalance.customer_key == customer_key,
            DailySpendBalance.business_date == business_date,
        )
    )
    if balance is None:
        balance = DailySpendBalance(
            store_id=store_id,
            customer_key=customer_key,
            business_date=business_date,
            total_amount=0,
        )
        db.add(balance)
    balance.total_amount += amount
    db.flush()

    tiers = db.scalars(
        select(RewardTier)
        .where(
            RewardTier.store_id == store_id,
            RewardTier.threshold_amount <= balance.total_amount,
        )
        .order_by(RewardTier.sort_order, RewardTier.threshold_amount)
    ).all()
    grants: list[RewardGrant] = []
    for tier in tiers:
        existing = db.scalar(
            select(RewardGrant).where(
                RewardGrant.store_id == store_id,
                RewardGrant.customer_key == customer_key,
                RewardGrant.business_date == business_date,
                RewardGrant.tier_id == tier.id,
            )
        )
        if existing is None:
            grant = RewardGrant(
                store_id=store_id,
                customer_key=customer_key,
                business_date=business_date,
                tier_id=tier.id,
            )
            db.add(grant)
            db.flush()
            grants.append(grant)
    return balance.total_amount, grants


def choose_benefit(
    db: Session, *, grant: RewardGrant, benefit: RewardBenefit, fulfill_mode: str
) -> tuple[Coupon | None, dict]:
    if grant.status != "AWAITING_CHOICE":
        raise ValueError("이미 선택이 완료된 리워드입니다.")
    if benefit.tier_id != grant.tier_id:
        raise ValueError("리워드 혜택이 해당 티어에 속하지 않습니다.")
    grant.status = "FULFILLED"
    grant.chosen_benefit_id = benefit.id
    grant.fulfill_mode = fulfill_mode
    coupon = None
    if fulfill_mode == "COUPON_7D":
        coupon = Coupon(
            store_id=grant.store_id,
            customer_key=grant.customer_key,
            grant_id=grant.id,
            benefit_snapshot={
                "benefitId": benefit.id,
                "type": benefit.benefit_type,
                "title": benefit.title,
                "payload": benefit.payload,
            },
            expires_at=db_now() + timedelta(days=7),
        )
        db.add(coupon)
    db.flush()
    return coupon, {
        "benefitId": benefit.id,
        "type": benefit.benefit_type,
        "title": benefit.title,
        "payload": benefit.payload,
    }
