from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from ai_ops.models import AIRecommendation
from catalog.models import Product, ProductCategory
from stores.models import Store, StoreMembership
from wifi.models import WiFiAmountTier, WiFiPolicy
from rewards.models import RewardTier, RewardTierBenefit


class Command(BaseCommand):
    help = "PDF 축소 MVP 시연용 매장·메뉴·정책·리워드·AI 추천을 멱등적으로 생성합니다."

    def add_arguments(self, parser):
        parser.add_argument("--store-name", default="펭귄 카페 MVP")
        parser.add_argument("--username", default="demo-owner")
        parser.add_argument("--password", default="demo-password")

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"]
        password = options["password"]

        user, user_created = User.objects.get_or_create(username=username)
        if user_created:
            user.set_password(password)
            user.save(update_fields=["password"])

        store, _ = Store.objects.get_or_create(
            name=options["store_name"],
            defaults={"timezone": "Asia/Seoul", "business_day_cutoff": time(0, 0)},
        )
        StoreMembership.objects.update_or_create(
            store=store,
            user=user,
            defaults={"role": StoreMembership.Role.OWNER},
        )

        category, _ = ProductCategory.objects.get_or_create(
            store=store,
            name="음료",
            defaults={"kind": ProductCategory.Kind.DRINK},
        )
        product, _ = Product.objects.get_or_create(
            store=store,
            name="아메리카노",
            defaults={"category": category, "price": 5000},
        )

        policy, _ = WiFiPolicy.objects.get_or_create(
            store=store,
            version=1,
            defaults={"base_minutes": 120, "is_published": True},
        )
        if not policy.is_published:
            policy.is_published = True
            policy.save(update_fields=["is_published"])
        for order_type, min_amount, bonus_minutes in [
            (WiFiAmountTier.OrderType.FIRST, 10000, 30),
            (WiFiAmountTier.OrderType.FIRST, 15000, 60),
            (WiFiAmountTier.OrderType.ADDITIONAL, 5000, 60),
            (WiFiAmountTier.OrderType.ADDITIONAL, 10000, 120),
        ]:
            WiFiAmountTier.objects.update_or_create(
                policy=policy,
                order_type=order_type,
                min_amount=min_amount,
                defaults={"bonus_minutes": bonus_minutes},
            )

        benefit_specs = [
            (
                RewardTierBenefit.BenefitType.FREE_SIZE_UP,
                "무료 사이즈업",
                {},
            ),
            (
                RewardTierBenefit.BenefitType.FREE_SHOT,
                "샷 추가",
                {"count": 1},
            ),
            (
                RewardTierBenefit.BenefitType.DESSERT_DISCOUNT,
                "디저트 1,000원 할인",
                {"discountAmount": 1000},
            ),
        ]
        for threshold, name in [(5000, "5천원 리워드"), (10000, "1만원 리워드")]:
            tier, _ = RewardTier.objects.get_or_create(
                store=store,
                threshold_amount=threshold,
                defaults={"name": name, "sort_order": 1 if threshold == 5000 else 2},
            )
            for benefit_type, title, payload in benefit_specs:
                RewardTierBenefit.objects.get_or_create(
                    tier=tier,
                    benefit_type=benefit_type,
                    title=title,
                    defaults={"payload": payload},
                )

        title = "오후 2~4시 아메리카노 15% 할인 추천"
        local_now = timezone.localtime()
        starts_at = local_now.replace(hour=14, minute=0, second=0, microsecond=0)
        if starts_at <= local_now:
            starts_at += timedelta(days=1)
        ends_at = starts_at + timedelta(hours=2)
        pending = [
            item
            for item in AIRecommendation.objects.filter(
                store=store,
                type=AIRecommendation.Type.TIME_SALE,
                status=AIRecommendation.Status.PENDING,
            )
            if item.payload.get("title") == title
        ]
        recommendation = pending[0] if pending else AIRecommendation.objects.create(
            store=store,
            type=AIRecommendation.Type.TIME_SALE,
            payload={
                "title": title,
                "menuName": product.name,
                "discountRate": 15,
                "startsAt": starts_at.isoformat(),
                "endsAt": ends_at.isoformat(),
                "source": "MVP_SEED",
            },
            reason="오후 2~4시 한산 시간대의 아메리카노 판매를 촉진합니다.",
            evidence={"seed": True},
            confidence="0.900",
        )

        self.stdout.write(self.style.SUCCESS("PDF MVP 데모 데이터 준비 완료"))
        self.stdout.write(f"storeId: {store.id}")
        self.stdout.write(f"productId: {product.id}")
        self.stdout.write(f"recommendationId: {recommendation.id}")
        self.stdout.write(f"username: {username}")
        if user_created:
            self.stdout.write(f"password: {password}")
