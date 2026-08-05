import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.auth import hash_password
from app.db import init_db, session_scope
from app.models import (
    AdminUser,
    AIRecommendation,
    Product,
    RewardBenefit,
    RewardTier,
    Store,
)


def seed(
    *,
    store_name: str = "펭귄 카페 MVP",
    username: str = "demo-owner",
    password: str = "demo-password",
) -> dict[str, str]:
    with session_scope() as db:
        store = db.scalar(select(Store).where(Store.name == store_name))
        if store is None:
            store = Store(name=store_name, timezone="Asia/Seoul", business_day_cutoff="00:00")
            db.add(store)
            db.flush()

        product = db.scalar(
            select(Product).where(Product.store_id == store.id, Product.name == "아메리카노")
        )
        if product is None:
            product = Product(store_id=store.id, name="아메리카노", price=5000)
            db.add(product)

        for threshold, name, sort_order in [
            (5000, "5천원 리워드", 1),
            (10000, "1만원 리워드", 2),
        ]:
            tier = db.scalar(
                select(RewardTier).where(
                    RewardTier.store_id == store.id,
                    RewardTier.threshold_amount == threshold,
                )
            )
            if tier is None:
                tier = RewardTier(
                    store_id=store.id,
                    name=name,
                    threshold_amount=threshold,
                    sort_order=sort_order,
                )
                db.add(tier)
                db.flush()
            for benefit_type, title, benefit_payload in [
                ("FREE_SIZE_UP", "무료 사이즈업", {}),
                ("FREE_SHOT", "샷 추가", {"count": 1}),
                ("DESSERT_DISCOUNT", "디저트 1,000원 할인", {"discountAmount": 1000}),
            ]:
                existing = db.scalar(
                    select(RewardBenefit).where(
                        RewardBenefit.tier_id == tier.id,
                        RewardBenefit.title == title,
                    )
                )
                if existing is None:
                    db.add(
                        RewardBenefit(
                            tier_id=tier.id,
                            benefit_type=benefit_type,
                            title=title,
                            payload=benefit_payload,
                        )
                    )

        day_pass_tier = db.scalar(
            select(RewardTier).where(
                RewardTier.store_id == store.id,
                RewardTier.threshold_amount == 20000,
            )
        )
        if day_pass_tier is None:
            day_pass_tier = RewardTier(
                store_id=store.id,
                name="2만원 리워드",
                threshold_amount=20000,
                sort_order=3,
            )
            db.add(day_pass_tier)
            db.flush()
        day_pass = db.scalar(
            select(RewardBenefit).where(
                RewardBenefit.tier_id == day_pass_tier.id,
                RewardBenefit.benefit_type == "WIFI_DAY_PASS",
            )
        )
        if day_pass is None:
            db.add(
                RewardBenefit(
                    tier_id=day_pass_tier.id,
                    benefit_type="WIFI_DAY_PASS",
                    title="Wi-Fi 종일권",
                    payload={"until": "BUSINESS_DAY_END"},
                )
            )
            db.add(
                RewardBenefit(
                    tier_id=day_pass_tier.id,
                    benefit_type="DRINK_DISCOUNT",
                    title="음료 할인",
                    payload={"discountRate": 10},
                )
            )

        admin_user = db.scalar(select(AdminUser).where(AdminUser.username == username))
        if admin_user is None:
            admin_user = AdminUser(
                store_id=store.id,
                username=username,
                password_hash=hash_password(password),
                role="OWNER",
            )
            db.add(admin_user)
        else:
            admin_user.store_id = store.id
            admin_user.password_hash = hash_password(password)

        title = "오후 2~4시 아메리카노 15% 할인 추천"
        recommendation = None
        pending = db.scalars(
            select(AIRecommendation).where(
                AIRecommendation.store_id == store.id,
                AIRecommendation.type == "TIME_SALE",
                AIRecommendation.status == "PENDING",
            )
        ).all()
        for item in pending:
            if item.payload.get("title") == title:
                recommendation = item
                break
        if recommendation is None:
            starts_at = datetime.now(timezone.utc) + timedelta(hours=1)
            ends_at = starts_at + timedelta(hours=2)
            recommendation = AIRecommendation(
                store_id=store.id,
                type="TIME_SALE",
                payload={
                    "title": title,
                    "menuName": "아메리카노",
                    "discountRate": 15,
                    "startsAt": starts_at.isoformat(),
                    "endsAt": ends_at.isoformat(),
                    "source": "MVP_SEED",
                },
                reason="오후 2~4시 아메리카노 판매를 촉진합니다.",
            )
            db.add(recommendation)
        db.flush()
        return {
            "storeId": store.id,
            "productId": product.id,
            "adminId": admin_user.id,
            "recommendationId": recommendation.id,
            "username": username,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart WiFi Pass PDF MVP seed")
    parser.add_argument("--store-name", default="펭귄 카페 MVP")
    parser.add_argument("--username", default="demo-owner")
    parser.add_argument("--password", default="demo-password")
    args = parser.parse_args()
    init_db()
    result = seed(
        store_name=args.store_name,
        username=args.username,
        password=args.password,
    )
    print("PDF MVP seed complete")
    for key, value in result.items():
        print(f"{key}: {value}")
    print(f"password: {args.password}")


if __name__ == "__main__":
    main()
