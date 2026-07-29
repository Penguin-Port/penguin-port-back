from datetime import timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from django.urls import URLPattern
from rest_framework.test import APIClient
import yaml

from ai_ops.models import AIRecommendation, Promotion
from catalog.models import Product, ProductCategory
from rewards.models import (
    Coupon,
    DailySpendBalance,
    ImmediateBenefitRedemption,
    RewardGrant,
    RewardTier,
    RewardTierBenefit,
)
from rewards.services import apply_immediate_redemption, choose_benefit
from operations.models import AuditLog
from stores.models import Store, StoreMembership
from wifi.models import ScheduledAction, WiFiAmountTier, WiFiPass, WiFiPolicy
from wifi.workers import execute_scheduled_action


class PurchaseRewardWifiFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.paid_at = timezone.now()
        self.store = Store.objects.create(name="펭귄 카페")
        category = ProductCategory.objects.create(
            store=self.store,
            name="음료",
            kind=ProductCategory.Kind.DRINK,
        )
        self.product = Product.objects.create(
            store=self.store,
            category=category,
            name="아메리카노",
            price=5000,
        )
        self.tier = RewardTier.objects.create(
            store=self.store,
            name="5천원 리워드",
            threshold_amount=5000,
            sort_order=1,
        )
        self.benefit = RewardTierBenefit.objects.create(
            tier=self.tier,
            benefit_type=RewardTierBenefit.BenefitType.FREE_SIZE_UP,
            title="무료 사이즈업",
        )
        policy = WiFiPolicy.objects.create(
            store=self.store,
            version=1,
            base_minutes=120,
            is_published=True,
        )
        WiFiAmountTier.objects.create(
            policy=policy,
            order_type=WiFiAmountTier.OrderType.FIRST,
            min_amount=5000,
            bonus_minutes=30,
        )
        WiFiAmountTier.objects.create(
            policy=policy,
            order_type=WiFiAmountTier.OrderType.ADDITIONAL,
            min_amount=5000,
            bonus_minutes=60,
        )

    def create_order(self, external_order_id, idempotency_key=None):
        return self.client.post(
            "/api/v1/pos/orders",
            {
                "storeId": str(self.store.id),
                "externalOrderId": external_order_id,
                "customer": {"phone": "010-1234-5678"},
                "items": [
                    {
                        "productId": str(self.product.id),
                        "quantity": 1,
                        "unitPrice": 5000,
                    }
                ],
                "totalAmount": 5000,
                "paidAt": self.paid_at.isoformat(),
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY=idempotency_key or f"idem-{external_order_id}",
        )

    def test_order_creates_balance_reward_wifi_and_claim(self):
        response = self.create_order("ORDER-1")

        self.assertEqual(response.status_code, 201)
        data = response.json()["data"]
        self.assertEqual(data["dailyTotal"], 5000)
        self.assertEqual(len(data["newRewardGrantIds"]), 1)
        self.assertEqual(data["wifiPass"]["breakdown"][0]["minutes"], 120)
        self.assertEqual(data["wifiPass"]["breakdown"][1]["minutes"], 30)
        self.assertEqual(DailySpendBalance.objects.get().total_amount, 5000)
        self.assertEqual(RewardGrant.objects.count(), 1)
        self.assertEqual(WiFiPass.objects.count(), 1)

    def test_second_order_extends_pass_without_duplicate_tier(self):
        first = self.create_order("ORDER-1").json()["data"]
        first_expiry = timezone.datetime.fromisoformat(first["wifiPass"]["expiresAt"])
        second_response = self.create_order("ORDER-2")

        self.assertEqual(second_response.status_code, 201)
        second = second_response.json()["data"]
        second_expiry = timezone.datetime.fromisoformat(second["wifiPass"]["expiresAt"])
        self.assertEqual(second["dailyTotal"], 10000)
        self.assertEqual(second["newRewardGrantIds"], [])
        self.assertEqual(second["wifiPass"]["breakdown"][0]["minutes"], 60)
        self.assertAlmostEqual(second_expiry - first_expiry, timedelta(minutes=60))
        self.assertEqual(RewardGrant.objects.count(), 1)

    def test_claim_exchange_activate_and_choose_coupon(self):
        order_data = self.create_order("ORDER-1").json()["data"]
        exchange = self.client.post(
            "/api/v1/public/order-claims/exchange",
            {"orderClaim": order_data["orderClaim"]["token"]},
            format="json",
        )
        self.assertEqual(exchange.status_code, 200)
        verification_ticket = exchange.json()["data"]["verificationTicket"]
        pass_id = exchange.json()["data"]["passId"]
        start = self.client.post(
            "/api/v1/public/verifications/start",
            {
                "verificationTicket": verification_ticket,
                "phone": "010-1234-5678",
            },
            format="json",
        )
        self.assertEqual(start.status_code, 201)
        confirm = self.client.post(
            "/api/v1/public/verifications/confirm",
            {
                "challengeId": start.json()["data"]["challengeId"],
                "code": start.json()["data"]["demoCode"],
            },
            format="json",
        )
        self.assertEqual(confirm.status_code, 200)
        portal_session = confirm.json()["data"]["portalSession"]

        activate = self.client.post(
            f"/api/v1/public/passes/{pass_id}/activate",
            {},
            format="json",
            HTTP_X_PORTAL_SESSION=portal_session,
        )
        self.assertEqual(activate.status_code, 200)
        self.assertEqual(activate.json()["data"]["status"], WiFiPass.Status.ACTIVE)

        grant_id = order_data["newRewardGrantIds"][0]
        choose = self.client.post(
            f"/api/v1/public/rewards/grants/{grant_id}/choose",
            {
                "benefitId": str(self.benefit.id),
                "fulfillMode": RewardGrant.FulfillMode.COUPON_7D,
            },
            format="json",
            HTTP_X_PORTAL_SESSION=portal_session,
        )
        self.assertEqual(choose.status_code, 200)
        self.assertIsNotNone(choose.json()["data"]["coupon"])
        self.assertEqual(Coupon.objects.count(), 1)

        coupon_id = choose.json()["data"]["coupon"]["couponId"]
        redeem = self.client.post(
            f"/api/v1/public/coupons/{coupon_id}/redeem",
            {},
            format="json",
            HTTP_X_PORTAL_SESSION=portal_session,
        )
        self.assertEqual(redeem.status_code, 200)
        self.assertEqual(redeem.json()["data"]["status"], Coupon.Status.REDEEMED)

    def test_duplicate_external_order_is_rejected(self):
        self.assertEqual(self.create_order("ORDER-1").status_code, 201)
        duplicate = self.create_order("ORDER-1", idempotency_key="different-key")
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["code"], "DUPLICATE_ORDER")

    def test_idempotency_replays_original_response(self):
        first = self.create_order("ORDER-1", idempotency_key="same-key")
        second = self.create_order("ORDER-1", idempotency_key="same-key")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json()["data"]["orderId"], second.json()["data"]["orderId"])

    def test_stale_expiration_action_does_not_expire_extended_pass(self):
        first = self.create_order("ORDER-1").json()["data"]
        wifi_pass = WiFiPass.objects.get(id=first["wifiPass"]["passId"])
        old_action = ScheduledAction.objects.filter(wifi_pass=wifi_pass).first()
        self.create_order("ORDER-2")
        old_action.execute_at = timezone.now()
        old_action.save(update_fields=["execute_at"])
        result = execute_scheduled_action(old_action.id)
        wifi_pass.refresh_from_db()
        self.assertEqual(result, "STALE_VERSION_SKIPPED")
        self.assertNotEqual(wifi_pass.status, WiFiPass.Status.EXPIRED)

    def test_immediate_benefit_can_be_applied_to_order_once(self):
        order_data = self.create_order("ORDER-1").json()["data"]
        grant = RewardGrant.objects.get(id=order_data["newRewardGrantIds"][0])
        _, _, redemption = choose_benefit(
            grant_id=grant.id,
            customer_key=grant.customer_key,
            benefit_id=self.benefit.id,
            fulfill_mode=RewardGrant.FulfillMode.IMMEDIATE,
        )
        applied = apply_immediate_redemption(
            redemption_id=redemption.id,
            order_id=order_data["orderId"],
            store_id=self.store.id,
        )
        self.assertEqual(applied.status, ImmediateBenefitRedemption.Status.APPLIED)
        with self.assertRaises(ValueError):
            apply_immediate_redemption(
                redemption_id=redemption.id,
                order_id=order_data["orderId"],
                store_id=self.store.id,
            )

    def test_pos_hmac_signature_is_verified_when_secret_is_configured(self):
        payload = {
            "storeId": str(self.store.id),
            "externalOrderId": "SIGNED-ORDER",
            "customer": {"phone": "010-1234-5678"},
            "items": [
                {
                    "productId": str(self.product.id),
                    "quantity": 1,
                    "unitPrice": 5000,
                }
            ],
            "totalAmount": 5000,
            "paidAt": self.paid_at.isoformat(),
        }
        body = json.dumps(payload).encode()
        timestamp = timezone.now().isoformat()
        nonce = f"nonce-{self.store.id}"
        signature = hmac.new(
            b"pos-secret",
            timestamp.encode() + nonce.encode() + body,
            hashlib.sha256,
        ).hexdigest()
        with mock.patch.dict(os.environ, {"POS_HMAC_SECRET": "pos-secret"}):
            response = self.client.generic(
                "POST",
                "/api/v1/pos/orders",
                data=body,
                content_type="application/json",
                HTTP_IDEMPOTENCY_KEY="signed-idempotency",
                HTTP_X_TIMESTAMP=timestamp,
                HTTP_X_NONCE=nonce,
                HTTP_X_SIGNATURE=signature,
            )
        self.assertEqual(response.status_code, 201)


class AdminApiTests(PurchaseRewardWifiFlowTests):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="owner", password="test-password"
        )
        StoreMembership.objects.create(
            store=self.store, user=self.user, role=StoreMembership.Role.OWNER
        )
        self.client.force_login(self.user)

    def test_refund_rolls_back_daily_total_and_writes_audit(self):
        response = self.create_order("ORDER-REFUND")
        order_id = response.json()["data"]["orderId"]
        refund = self.client.post(
            f"/api/v1/admin/orders/{order_id}/refund",
            {"storeId": str(self.store.id), "refundAmount": 5000},
            format="json",
        )
        self.assertEqual(refund.status_code, 200)
        self.assertEqual(refund.json()["data"]["dailyTotal"], 0)
        self.assertEqual(AuditLog.objects.filter(action="ORDER_REFUND").count(), 1)

    def test_wifi_policy_simulation(self):
        response = self.client.post(
            "/api/v1/admin/wifi/policies/simulate",
            {
                "storeId": str(self.store.id),
                "orderType": "FIRST",
                "amount": 5000,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["totalMinutes"], 150)

    def test_ai_recommendation_accept_creates_promotion(self):
        recommendation = AIRecommendation.objects.create(
            store=self.store,
            type=AIRecommendation.Type.TIME_SALE,
            payload={"discountRate": 15},
            reason="한산 시간대",
        )
        starts_at = timezone.now() + timedelta(hours=1)
        response = self.client.post(
            f"/api/v1/admin/ai/recommendations/{recommendation.id}/accept",
            {
                "storeId": str(self.store.id),
                "version": 1,
                "title": "오후 타임세일",
                "payload": {"discountRate": 15},
                "startsAt": starts_at.isoformat(),
                "endsAt": (starts_at + timedelta(hours=2)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Promotion.objects.count(), 1)

    def test_inventory_risk_scan_creates_recommendation(self):
        create = self.client.post(
            "/api/v1/admin/inventory",
            {
                "storeId": str(self.store.id),
                "productId": str(self.product.id),
                "quantity": "10.00",
                "unit": "EA",
                "lowStockThreshold": "2.00",
                "expiresOn": timezone.localdate().isoformat(),
            },
            format="json",
        )
        self.assertEqual(create.status_code, 201)
        scan = self.client.post(
            "/api/v1/admin/inventory/scan",
            {"storeId": str(self.store.id)},
            format="json",
        )
        self.assertEqual(scan.status_code, 200)
        self.assertEqual(len(scan.json()["data"]["recommendationIds"]), 1)

    def test_admin_jwt_login_and_refresh_rotation(self):
        client = APIClient()
        login_response = client.post(
            "/api/v1/admin/auth/login",
            {"username": "owner", "password": "test-password"},
            format="json",
        )
        self.assertEqual(login_response.status_code, 200)
        access_token = login_response.json()["data"]["accessToken"]
        self.assertIn("smartpass_refresh", login_response.cookies)

        me = client.get(
            "/api/v1/admin/auth/me",
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["data"]["username"], "owner")

        refresh = client.post("/api/v1/admin/auth/refresh", {}, format="json")
        self.assertEqual(refresh.status_code, 200)
        self.assertNotEqual(
            refresh.json()["data"]["accessToken"],
            access_token,
        )


class OpenApiCoverageTests(TestCase):
    def test_openapi_covers_every_api_url(self):
        from api.urls import urlpatterns

        parameter_names = {
            "pass_id": "passId",
            "grant_id": "grantId",
            "coupon_id": "couponId",
            "store_id": "storeId",
            "order_id": "orderId",
            "policy_id": "policyId",
            "item_id": "itemId",
            "recommendation_id": "recommendationId",
            "promotion_id": "promotionId",
            "redemption_id": "redemptionId",
        }
        django_paths = set()
        django_operations = set()
        for pattern in urlpatterns:
            self.assertIsInstance(pattern, URLPattern)
            path = "/" + str(pattern.pattern)
            for django_name, api_name in parameter_names.items():
                path = re.sub(
                    rf"<(?:uuid|int):{django_name}>",
                    "{" + api_name + "}",
                    path,
                )
            django_paths.add(path)
            view_class = pattern.callback.cls
            for method in ["get", "post", "put", "patch", "delete"]:
                if hasattr(view_class, method):
                    django_operations.add((path, method))

        spec_path = Path(__file__).resolve().parent.parent / "docs" / "openapi.yaml"
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        self.assertEqual(django_paths, set(spec["paths"]))
        openapi_operations = {
            (path, method)
            for path, path_item in spec["paths"].items()
            for method in path_item
            if method in ["get", "post", "put", "patch", "delete"]
        }
        self.assertEqual(django_operations, openapi_operations)
