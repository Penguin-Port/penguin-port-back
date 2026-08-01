import json
from datetime import timedelta

import jwt
from django.contrib.auth import authenticate, login, logout
from django.conf import settings
from django.db import transaction
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from ai_ops.models import AIRecommendation, AnalyticsHourly, Promotion
from ai_ops.services import (
    generate_menu_trend_fallback,
    generate_sales_summary,
    generate_time_sale_recommendation,
    reject_recommendation,
)
from api.access import require_store_access
from api.auth import create_portal_session, read_portal_session
from api.exceptions import problem_response
from api.serializers import (
    InventoryAdjustSerializer,
    InventoryCreateSerializer,
    LoginSerializer,
    ManualPassActionSerializer,
    MembershipUpsertSerializer,
    PassExtendSerializer,
    PrivacyRetentionSerializer,
    ProductCreateSerializer,
    PromotionUpdateSerializer,
    RecommendationEditSerializer,
    RecommendationRejectSerializer,
    RefundSerializer,
    RewardTierCreateSerializer,
    StoreSettingsSerializer,
    VerificationConfirmSerializer,
    VerificationStartSerializer,
    WiFiPolicySerializer,
    WiFiPolicySimulateSerializer,
)
from api.views import pass_data, success
from catalog.models import Product, ProductCategory
from identity.models import (
    RefreshTokenSession,
    UserIdentity,
    VerificationChallenge,
    get_user_public_id,
)
from identity.services import confirm_verification, start_verification
from identity.tokens import issue_token_pair, revoke_refresh_token, rotate_refresh_token
from inventory.models import InventoryItem
from inventory.services import adjust_inventory, calculate_risk_score, scan_inventory_risk
from operations.models import AuditLog, Notification, OutboxEvent
from operations.services import emit_event, get_or_create_retention_policy, write_audit
from orders.models import Order
from orders.services import refund_order
from rewards.models import Coupon, RewardTier, RewardTierBenefit
from rewards.services import redeem_coupon
from stores.models import Store, StoreMembership
from wifi.adapters import get_network_adapter
from wifi.models import PassExtension, WiFiAmountTier, WiFiPass, WiFiPolicy


OWNER_MANAGER = [StoreMembership.Role.OWNER, StoreMembership.Role.MANAGER]


def _access_error(request, exc):
    return problem_response(
        request=request,
        detail=str(exc),
        code="STORE_ACCESS_DENIED",
        status=403,
    )


class VerificationStartView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_scope = "verification"

    def post(self, request):
        serializer = VerificationStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            challenge, demo_code = start_verification(**serializer.validated_data)
        except (ValueError, Order.DoesNotExist) as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="VERIFICATION_START_FAILED",
                status=422,
            )
        return success(
            request,
            {
                "challengeId": str(challenge.id),
                "expiresAt": challenge.expires_at.isoformat(),
                "maxAttempts": challenge.max_attempts,
                "demoCode": demo_code,
            },
            status=201,
        )


class VerificationConfirmView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_scope = "verification"

    def post(self, request):
        serializer = VerificationConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            challenge = confirm_verification(**serializer.validated_data)
        except VerificationChallenge.DoesNotExist:
            return problem_response(
                request=request,
                detail="인증 요청을 찾을 수 없습니다.",
                code="VERIFICATION_NOT_FOUND",
                status=404,
            )
        except ValueError as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="VERIFICATION_FAILED",
                status=422,
            )
        portal_session = create_portal_session(
            customer_key=challenge.customer_key,
            store_id=str(challenge.store_id),
        )
        wifi_pass = WiFiPass.objects.get(
            store=challenge.store,
            customer_key=challenge.customer_key,
            business_date=challenge.order.business_date,
        )
        return success(
            request,
            {
                "portalSession": portal_session,
                "passId": str(wifi_pass.id),
                "expiresIn": 86400,
            },
        )


class CouponListView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        try:
            session = read_portal_session(request)
        except PermissionError as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="PORTAL_SESSION_INVALID",
                status=401,
            )
        coupons = Coupon.objects.filter(
            store_id=session["storeId"], customer_key=session["customerKey"]
        ).order_by("-created_at")
        return success(
            request,
            [
                {
                    "couponId": str(coupon.id),
                    "status": coupon.status,
                    "benefit": coupon.benefit_snapshot,
                    "expiresAt": coupon.expires_at.isoformat(),
                    "redeemedAt": (
                        coupon.redeemed_at.isoformat() if coupon.redeemed_at else None
                    ),
                }
                for coupon in coupons
            ],
        )


class CouponRedeemView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, coupon_id):
        try:
            session = read_portal_session(request)
            coupon = redeem_coupon(
                coupon_id=coupon_id, customer_key=session["customerKey"]
            )
        except PermissionError as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="PORTAL_SESSION_INVALID",
                status=401,
            )
        except Coupon.DoesNotExist:
            return problem_response(
                request=request,
                detail="쿠폰을 찾을 수 없습니다.",
                code="COUPON_NOT_FOUND",
                status=404,
            )
        except ValueError as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="COUPON_NOT_REDEEMABLE",
                status=409,
            )
        return success(
            request,
            {
                "couponId": str(coupon.id),
                "status": coupon.status,
                "redeemedAt": coupon.redeemed_at.isoformat(),
            },
        )


class PrivacyNoticeView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, store_id):
        try:
            store = Store.objects.get(id=store_id)
        except Store.DoesNotExist:
            return problem_response(
                request=request,
                detail="매장을 찾을 수 없습니다.",
                code="STORE_NOT_FOUND",
                status=404,
            )
        policy = get_or_create_retention_policy(store)
        return success(
            request,
            {
                "storeId": str(store.id),
                "noticeText": policy.notice_text,
                "phoneRetentionDays": policy.phone_retention_days,
                "verificationRetentionDays": policy.verification_retention_days,
            },
        )


class AdminLoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(request, **serializer.validated_data)
        if user is None:
            return problem_response(
                request=request,
                detail="아이디 또는 비밀번호가 올바르지 않습니다.",
                code="LOGIN_FAILED",
                status=401,
            )
        login(request, user)
        pair = issue_token_pair(user)
        response = success(
            request,
            {
                "userId": str(get_user_public_id(user)),
                "username": user.username,
                "accessToken": pair["accessToken"],
                "accessExpiresIn": pair["accessExpiresIn"],
            },
        )
        response.set_cookie(
            "smartpass_refresh",
            pair["refreshToken"],
            max_age=pair["refreshExpiresIn"],
            httponly=True,
            secure=not settings.DEBUG,
            samesite="Lax",
            path="/api/v1/admin/auth",
        )
        return response


class AdminRefreshView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.COOKIES.get("smartpass_refresh")
        if not token:
            return problem_response(
                request=request,
                detail="Refresh Token Cookie가 필요합니다.",
                code="REFRESH_TOKEN_REQUIRED",
                status=401,
            )
        try:
            pair = rotate_refresh_token(token)
        except (jwt.InvalidTokenError, RefreshTokenSession.DoesNotExist, ValueError) as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="REFRESH_TOKEN_INVALID",
                status=401,
            )
        response = success(
            request,
            {
                "accessToken": pair["accessToken"],
                "accessExpiresIn": pair["accessExpiresIn"],
            },
        )
        response.set_cookie(
            "smartpass_refresh",
            pair["refreshToken"],
            max_age=pair["refreshExpiresIn"],
            httponly=True,
            secure=not settings.DEBUG,
            samesite="Lax",
            path="/api/v1/admin/auth",
        )
        return response


class AdminLogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.COOKIES.get("smartpass_refresh")
        if token:
            try:
                revoke_refresh_token(token)
            except (jwt.InvalidTokenError, RefreshTokenSession.DoesNotExist, ValueError):
                pass
        logout(request)
        response = success(request, {"loggedOut": True})
        response.delete_cookie("smartpass_refresh", path="/api/v1/admin/auth")
        return response


class AdminMeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        memberships = request.user.store_memberships.select_related("store")
        return success(
            request,
            {
                "userId": str(get_user_public_id(request.user)),
                "username": request.user.username,
                "memberships": [
                    {
                        "storeId": str(item.store_id),
                        "storeName": item.store.name,
                        "role": item.role,
                    }
                    for item in memberships
                ],
            },
        )


class AdminOrderRefundView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):
        serializer = RefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            store = require_store_access(
                request.user, serializer.validated_data["store_id"], roles=OWNER_MANAGER
            )
            order, balance, revoked = refund_order(
                order_id=order_id,
                store=store,
                refund_amount=serializer.validated_data["refund_amount"],
            )
        except PermissionError as exc:
            return _access_error(request, exc)
        except Order.DoesNotExist:
            return problem_response(
                request=request,
                detail="주문을 찾을 수 없습니다.",
                code="ORDER_NOT_FOUND",
                status=404,
            )
        except ValueError as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="REFUND_RULE_VIOLATION",
                status=422,
            )
        write_audit(
            store=store,
            actor=request.user,
            action="ORDER_REFUND",
            resource_type="Order",
            resource_id=order.id,
            after={"refundedAmount": order.refunded_amount},
        )
        return success(
            request,
            {
                "orderId": str(order.id),
                "status": order.status,
                "refundedAmount": order.refunded_amount,
                "dailyTotal": balance.total_amount,
                "revokedRewardGrantIds": revoked,
            },
        )


class AdminOrderListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get("storeId")
        try:
            store = require_store_access(request.user, store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        orders = Order.objects.filter(store=store).prefetch_related("items").order_by(
            "-paid_at"
        )[:500]
        return success(
            request,
            [
                {
                    "orderId": str(order.id),
                    "externalOrderId": order.external_order_id,
                    "status": order.status,
                    "totalAmount": order.total_amount,
                    "refundedAmount": order.refunded_amount,
                    "businessDate": str(order.business_date),
                    "paidAt": order.paid_at.isoformat(),
                    "phoneLast4": order.phone_last4,
                    "items": [
                        {
                            "productId": str(item.product_id),
                            "name": item.name_snapshot,
                            "quantity": item.quantity,
                            "unitPrice": item.unit_price,
                        }
                        for item in order.items.all()
                    ],
                }
                for order in orders
            ],
        )


class AdminCatalogListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get("storeId")
        try:
            store = require_store_access(request.user, store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        products = Product.objects.filter(store=store).select_related("category")
        return success(
            request,
            [
                {
                    "productId": str(item.id),
                    "categoryId": str(item.category_id),
                    "category": item.category.name,
                    "kind": item.category.kind,
                    "name": item.name,
                    "price": item.price,
                    "isActive": item.is_active,
                }
                for item in products
            ],
        )

    def post(self, request):
        serializer = ProductCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            store = require_store_access(
                request.user, data["store_id"], roles=OWNER_MANAGER
            )
        except PermissionError as exc:
            return _access_error(request, exc)
        category = ProductCategory.objects.get(id=data["category_id"], store=store)
        product = Product.objects.create(
            store=store,
            category=category,
            name=data["name"],
            price=data["price"],
        )
        return success(
            request,
            {
                "productId": str(product.id),
                "name": product.name,
                "price": product.price,
                "isActive": product.is_active,
            },
            status=201,
        )


class AdminWiFiPolicyListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get("storeId")
        try:
            store = require_store_access(request.user, store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        policies = WiFiPolicy.objects.filter(store=store).prefetch_related("amount_tiers")
        return success(request, [_policy_data(item) for item in policies])

    @transaction.atomic
    def post(self, request):
        serializer = WiFiPolicySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            store = require_store_access(
                request.user, data["store_id"], roles=OWNER_MANAGER
            )
        except PermissionError as exc:
            return _access_error(request, exc)
        version = (
            WiFiPolicy.objects.filter(store=store)
            .order_by("-version")
            .values_list("version", flat=True)
            .first()
            or 0
        ) + 1
        policy = WiFiPolicy.objects.create(
            store=store,
            version=version,
            base_minutes=data["base_minutes"],
            quiet_hours_enabled=data["quiet_hours_enabled"],
            quiet_hours_until=data.get("quiet_hours_until"),
        )
        WiFiAmountTier.objects.bulk_create(
            [
                WiFiAmountTier(
                    policy=policy,
                    order_type=item["orderType"],
                    min_amount=item["minAmount"],
                    bonus_minutes=item["bonusMinutes"],
                )
                for item in data["amount_tiers"]
            ]
        )
        write_audit(
            store=store,
            actor=request.user,
            action="WIFI_POLICY_CREATE",
            resource_type="WiFiPolicy",
            resource_id=policy.id,
            after=_policy_data(policy),
        )
        return success(request, _policy_data(policy), status=201)


def _policy_data(policy):
    return {
        "policyId": policy.id,
        "version": policy.version,
        "baseMinutes": policy.base_minutes,
        "quietHoursEnabled": policy.quiet_hours_enabled,
        "quietHoursUntil": (
            str(policy.quiet_hours_until) if policy.quiet_hours_until else None
        ),
        "isPublished": policy.is_published,
        "amountTiers": [
            {
                "orderType": tier.order_type,
                "minAmount": tier.min_amount,
                "bonusMinutes": tier.bonus_minutes,
            }
            for tier in policy.amount_tiers.all()
        ],
    }


class AdminWiFiPolicyPublishView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, policy_id):
        policy = WiFiPolicy.objects.select_for_update().select_related("store").get(
            id=policy_id
        )
        try:
            require_store_access(request.user, policy.store_id, roles=OWNER_MANAGER)
        except PermissionError as exc:
            return _access_error(request, exc)
        WiFiPolicy.objects.filter(store=policy.store, is_published=True).update(
            is_published=False
        )
        policy.is_published = True
        policy.save(update_fields=["is_published"])
        write_audit(
            store=policy.store,
            actor=request.user,
            action="WIFI_POLICY_PUBLISH",
            resource_type="WiFiPolicy",
            resource_id=policy.id,
        )
        return success(request, _policy_data(policy))


class AdminWiFiPolicySimulateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = WiFiPolicySimulateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            store = require_store_access(request.user, data["store_id"])
        except PermissionError as exc:
            return _access_error(request, exc)
        policy = (
            WiFiPolicy.objects.filter(store=store, is_published=True)
            .order_by("-version")
            .first()
        )
        if policy is None:
            return problem_response(
                request=request,
                detail="게시된 Wi-Fi 정책이 없습니다.",
                code="WIFI_POLICY_NOT_FOUND",
                status=404,
            )
        tier = (
            policy.amount_tiers.filter(
                order_type=data["order_type"], min_amount__lte=data["amount"]
            )
            .order_by("-min_amount")
            .first()
        )
        bonus = tier.bonus_minutes if tier else 0
        total = bonus + (
            policy.base_minutes if data["order_type"] == WiFiAmountTier.OrderType.FIRST else 0
        )
        return success(
            request,
            {
                "policyVersion": policy.version,
                "orderType": data["order_type"],
                "amount": data["amount"],
                "baseMinutes": (
                    policy.base_minutes
                    if data["order_type"] == WiFiAmountTier.OrderType.FIRST
                    else 0
                ),
                "bonusMinutes": bonus,
                "totalMinutes": total,
            },
        )


class AdminLivePassListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get("storeId")
        try:
            store = require_store_access(request.user, store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        passes = WiFiPass.objects.filter(store=store).order_by("-issued_at")[:200]
        return success(request, [pass_data(item) for item in passes])


class AdminPassExtensionListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pass_id):
        wifi_pass = WiFiPass.objects.get(id=pass_id)
        try:
            require_store_access(request.user, wifi_pass.store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        extensions = PassExtension.objects.filter(wifi_pass=wifi_pass).order_by("created_at")
        return success(
            request,
            [
                {
                    "extensionId": str(item.id),
                    "orderId": str(item.order_id),
                    "minutes": item.minutes,
                    "reason": item.reason,
                    "createdAt": item.created_at.isoformat(),
                }
                for item in extensions
            ],
        )


class AdminPassActionView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pass_id):
        serializer = ManualPassActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        wifi_pass = WiFiPass.objects.select_for_update().select_related("store").get(
            id=pass_id, store_id=data["store_id"]
        )
        try:
            require_store_access(request.user, wifi_pass.store_id, roles=OWNER_MANAGER)
        except PermissionError as exc:
            return _access_error(request, exc)
        before = pass_data(wifi_pass)
        if data["action"] == "EXTEND":
            wifi_pass.expires_at = max(wifi_pass.expires_at, timezone.now()) + timedelta(
                minutes=data["minutes"]
            )
            wifi_pass.pass_version += 1
            wifi_pass.save(update_fields=["expires_at", "pass_version"])
        elif data["action"] == "BLOCK":
            wifi_pass.status = WiFiPass.Status.BLOCKED
            wifi_pass.network_reference = ""
            wifi_pass.save(update_fields=["status", "network_reference"])
        elif data["action"] == "UNBLOCK":
            if wifi_pass.expires_at <= timezone.now():
                return problem_response(
                    request=request,
                    detail="만료된 이용권은 차단 해제할 수 없습니다.",
                    code="WIFI_PASS_EXPIRED",
                    status=409,
                )
            wifi_pass.status = WiFiPass.Status.ACTIVE
            wifi_pass.save(update_fields=["status"])
        write_audit(
            store=wifi_pass.store,
            actor=request.user,
            action=f"WIFI_PASS_{data['action']}",
            resource_type="WiFiPass",
            resource_id=wifi_pass.id,
            before=before,
            after=pass_data(wifi_pass),
        )
        return success(request, pass_data(wifi_pass))


class AdminPassExtendView(APIView):
    """PDF MVP의 POST /admin/passes/{id}/extend 호환 API."""

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pass_id):
        serializer = PassExtendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            wifi_pass = WiFiPass.objects.select_for_update().select_related("store").get(
                id=pass_id, store_id=data["store_id"]
            )
        except WiFiPass.DoesNotExist:
            return problem_response(
                request=request,
                detail="이용권을 찾을 수 없습니다.",
                code="WIFI_PASS_NOT_FOUND",
                status=404,
            )
        try:
            require_store_access(request.user, wifi_pass.store_id, roles=OWNER_MANAGER)
        except PermissionError as exc:
            return _access_error(request, exc)

        before = pass_data(wifi_pass)
        wifi_pass.expires_at = max(wifi_pass.expires_at, timezone.now()) + timedelta(
            minutes=data["minutes"]
        )
        wifi_pass.pass_version += 1
        if wifi_pass.status == WiFiPass.Status.EXPIRED:
            wifi_pass.status = WiFiPass.Status.ACTIVE
        wifi_pass.save(update_fields=["expires_at", "pass_version", "status"])
        write_audit(
            store=wifi_pass.store,
            actor=request.user,
            action="WIFI_PASS_EXTEND",
            resource_type="WiFiPass",
            resource_id=wifi_pass.id,
            before=before,
            after=pass_data(wifi_pass),
        )
        return success(request, pass_data(wifi_pass))


class AdminPassExpireView(APIView):
    """PDF MVP의 POST /admin/passes/{id}/expire 호환 API."""

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pass_id):
        try:
            wifi_pass = WiFiPass.objects.select_for_update().select_related("store").get(
                id=pass_id
            )
        except WiFiPass.DoesNotExist:
            return problem_response(
                request=request,
                detail="이용권을 찾을 수 없습니다.",
                code="WIFI_PASS_NOT_FOUND",
                status=404,
            )

        requested_store_id = request.data.get("storeId")
        if requested_store_id and str(wifi_pass.store_id) != str(requested_store_id):
            return problem_response(
                request=request,
                detail="요청 매장과 이용권 매장이 일치하지 않습니다.",
                code="STORE_ACCESS_DENIED",
                status=403,
            )
        try:
            require_store_access(request.user, wifi_pass.store_id, roles=OWNER_MANAGER)
        except PermissionError as exc:
            return _access_error(request, exc)

        before = pass_data(wifi_pass)
        if wifi_pass.status not in [
            WiFiPass.Status.EXPIRED,
            WiFiPass.Status.CANCELLED,
            WiFiPass.Status.BLOCKED,
        ]:
            get_network_adapter().revoke(reference=wifi_pass.network_reference or "")
            wifi_pass.status = WiFiPass.Status.EXPIRED
            wifi_pass.network_reference = ""
            wifi_pass.pass_version += 1
            wifi_pass.save(
                update_fields=["status", "network_reference", "pass_version"]
            )
            emit_event(
                store=wifi_pass.store,
                type="wifi.pass.expired",
                aggregate_type="WiFiPass",
                aggregate_id=wifi_pass.id,
                payload={"passId": str(wifi_pass.id), "source": "admin"},
            )
            write_audit(
                store=wifi_pass.store,
                actor=request.user,
                action="WIFI_PASS_EXPIRE",
                resource_type="WiFiPass",
                resource_id=wifi_pass.id,
                before=before,
                after=pass_data(wifi_pass),
            )
        return success(request, pass_data(wifi_pass))


class AdminRewardTierListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get("storeId")
        try:
            store = require_store_access(request.user, store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        tiers = RewardTier.objects.filter(store=store).prefetch_related("benefits")
        return success(request, [_tier_data(tier) for tier in tiers])

    @transaction.atomic
    def post(self, request):
        serializer = RewardTierCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            store = require_store_access(
                request.user, data["store_id"], roles=OWNER_MANAGER
            )
        except PermissionError as exc:
            return _access_error(request, exc)
        tier = RewardTier.objects.create(
            store=store,
            name=data["name"],
            threshold_amount=data["threshold_amount"],
            sort_order=data["sort_order"],
        )
        for item in data["benefits"]:
            RewardTierBenefit.objects.create(
                tier=tier,
                benefit_type=item["benefitType"],
                title=item["title"],
                payload=item.get("payload", {}),
            )
        return success(request, _tier_data(tier), status=201)


class AdminRewardHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get("storeId")
        try:
            store = require_store_access(request.user, store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        from rewards.models import RewardGrant

        grants = (
            RewardGrant.objects.filter(store=store)
            .select_related("tier", "chosen_benefit")
            .order_by("-created_at")[:500]
        )
        return success(
            request,
            [
                {
                    "grantId": str(item.id),
                    "businessDate": str(item.business_date),
                    "tierId": str(item.tier_id),
                    "tierAmount": item.tier.threshold_amount,
                    "status": item.status,
                    "chosenBenefitId": (
                        str(item.chosen_benefit_id) if item.chosen_benefit_id else None
                    ),
                    "fulfillMode": item.fulfill_mode,
                    "createdAt": item.created_at.isoformat(),
                }
                for item in grants
            ],
        )


def _tier_data(tier):
    return {
        "tierId": str(tier.id),
        "name": tier.name,
        "thresholdAmount": tier.threshold_amount,
        "sortOrder": tier.sort_order,
        "isActive": tier.is_active,
        "benefits": [
            {
                "benefitId": str(item.id),
                "benefitType": item.benefit_type,
                "title": item.title,
                "payload": item.payload,
            }
            for item in tier.benefits.all()
        ],
    }


class AdminInventoryListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get("storeId")
        try:
            store = require_store_access(request.user, store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        items = InventoryItem.objects.filter(store=store).select_related("product")
        return success(request, [_inventory_data(item) for item in items])

    def post(self, request):
        serializer = InventoryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            store = require_store_access(
                request.user, data["store_id"], roles=OWNER_MANAGER
            )
        except PermissionError as exc:
            return _access_error(request, exc)
        product = Product.objects.get(id=data["product_id"], store=store)
        item = InventoryItem.objects.create(
            store=store,
            product=product,
            quantity=data["quantity"],
            unit=data["unit"],
            low_stock_threshold=data["low_stock_threshold"],
            expires_on=data.get("expires_on"),
        )
        item.risk_score = calculate_risk_score(item)
        item.save(update_fields=["risk_score"])
        return success(request, _inventory_data(item), status=201)


def _inventory_data(item):
    return {
        "inventoryItemId": str(item.id),
        "productId": str(item.product_id),
        "productName": item.product.name,
        "quantity": str(item.quantity),
        "unit": item.unit,
        "lowStockThreshold": str(item.low_stock_threshold),
        "expiresOn": str(item.expires_on) if item.expires_on else None,
        "riskScore": float(item.risk_score),
    }


class AdminInventoryAdjustView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, item_id):
        serializer = InventoryAdjustSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = InventoryItem.objects.select_related("store", "product").get(id=item_id)
        try:
            require_store_access(request.user, item.store_id, roles=OWNER_MANAGER)
            item = adjust_inventory(item_id=item_id, **serializer.validated_data)
        except PermissionError as exc:
            return _access_error(request, exc)
        except ValueError as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="INVENTORY_RULE_VIOLATION",
                status=422,
            )
        return success(request, _inventory_data(item))


class AdminInventoryScanView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        store_id = request.data.get("storeId")
        try:
            store = require_store_access(request.user, store_id, roles=OWNER_MANAGER)
        except PermissionError as exc:
            return _access_error(request, exc)
        recommendations = scan_inventory_risk(store=store)
        return success(
            request,
            {"recommendationIds": [str(item.id) for item in recommendations]},
        )


class AdminRecommendationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get("storeId")
        try:
            store = require_store_access(request.user, store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        queryset = AIRecommendation.objects.filter(store=store).order_by("-created_at")
        if request.query_params.get("type"):
            queryset = queryset.filter(type=request.query_params["type"])
        return success(request, [_recommendation_data(item) for item in queryset[:200]])


def _recommendation_data(item):
    return {
        "recommendationId": str(item.id),
        "type": item.type,
        "payload": item.payload,
        "reason": item.reason,
        "evidence": item.evidence,
        "confidence": float(item.confidence) if item.confidence is not None else None,
        "status": item.status,
        "version": item.version,
        "createdAt": item.created_at.isoformat(),
    }


class AdminRecommendationGenerateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        store_id = request.data.get("storeId")
        recommendation_type = request.data.get("type")
        try:
            store = require_store_access(request.user, store_id, roles=OWNER_MANAGER)
        except PermissionError as exc:
            return _access_error(request, exc)
        if recommendation_type == AIRecommendation.Type.SALES_SUMMARY:
            generated = [generate_sales_summary(store=store)]
        elif recommendation_type == AIRecommendation.Type.TIME_SALE:
            generated = [generate_time_sale_recommendation(store=store)]
        elif recommendation_type == AIRecommendation.Type.MENU_TREND:
            generated = generate_menu_trend_fallback(store=store)
        elif recommendation_type == AIRecommendation.Type.INVENTORY_PROMOTION:
            generated = scan_inventory_risk(store=store)
        else:
            return problem_response(
                request=request,
                detail="지원하지 않는 추천 유형입니다.",
                code="AI_RECOMMENDATION_TYPE_INVALID",
                status=422,
            )
        return success(request, [_recommendation_data(item) for item in generated], status=201)


class AdminRecommendationRejectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, recommendation_id):
        serializer = RecommendationRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            store = require_store_access(
                request.user,
                serializer.validated_data["store_id"],
                roles=OWNER_MANAGER,
            )
            recommendation = reject_recommendation(
                recommendation_id=recommendation_id,
                store_id=store.id,
                reason=serializer.validated_data.get("reason", ""),
            )
        except PermissionError as exc:
            return _access_error(request, exc)
        except ValueError as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="RECOMMENDATION_INVALID_STATE",
                status=409,
            )
        return success(request, _recommendation_data(recommendation))


class AdminRecommendationEditView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, recommendation_id):
        serializer = RecommendationEditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            store = require_store_access(
                request.user, data["store_id"], roles=OWNER_MANAGER
            )
        except PermissionError as exc:
            return _access_error(request, exc)
        recommendation = AIRecommendation.objects.select_for_update().get(
            id=recommendation_id, store=store
        )
        if recommendation.status != AIRecommendation.Status.PENDING:
            return problem_response(
                request=request,
                detail="대기 중인 추천만 수정할 수 있습니다.",
                code="RECOMMENDATION_INVALID_STATE",
                status=409,
            )
        if recommendation.version != data["version"]:
            return problem_response(
                request=request,
                detail="추천 버전이 충돌했습니다.",
                code="RECOMMENDATION_VERSION_CONFLICT",
                status=409,
            )
        recommendation.payload = data["payload"]
        recommendation.reason = data.get("reason", recommendation.reason)
        recommendation.status = AIRecommendation.Status.EDITED
        recommendation.version += 1
        recommendation.save(update_fields=["payload", "reason", "status", "version"])
        return success(request, _recommendation_data(recommendation))


class AdminPromotionListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get("storeId")
        try:
            store = require_store_access(request.user, store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        promotions = Promotion.objects.filter(store=store).order_by("-created_at")
        return success(
            request,
            [
                {
                    "promotionId": str(item.id),
                    "title": item.title,
                    "payload": item.payload,
                    "status": item.status,
                    "startsAt": item.starts_at.isoformat(),
                    "endsAt": item.ends_at.isoformat(),
                }
                for item in promotions
            ],
        )


class AdminPromotionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, promotion_id):
        serializer = PromotionUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        promotion = Promotion.objects.select_for_update().select_related("store").get(
            id=promotion_id, store_id=data["store_id"]
        )
        try:
            require_store_access(request.user, promotion.store_id, roles=OWNER_MANAGER)
        except PermissionError as exc:
            return _access_error(request, exc)
        for field in ["title", "payload", "starts_at", "ends_at", "status"]:
            if field in data:
                setattr(promotion, field, data[field])
        if promotion.ends_at <= promotion.starts_at:
            return problem_response(
                request=request,
                detail="종료 시각은 시작 시각 이후여야 합니다.",
                code="PROMOTION_TIME_INVALID",
                status=422,
            )
        promotion.save()
        return success(
            request,
            {
                "promotionId": str(promotion.id),
                "title": promotion.title,
                "payload": promotion.payload,
                "status": promotion.status,
                "startsAt": promotion.starts_at.isoformat(),
                "endsAt": promotion.ends_at.isoformat(),
            },
        )


class AdminAnalyticsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get("storeId")
        try:
            store = require_store_access(request.user, store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        since = timezone.now() - timedelta(days=int(request.query_params.get("days", 7)))
        buckets = AnalyticsHourly.objects.filter(
            store=store, bucket_start__gte=since
        ).order_by("bucket_start")
        return success(
            request,
            [
                {
                    "bucketStart": item.bucket_start.isoformat(),
                    "orderCount": item.order_count,
                    "grossSales": item.gross_sales,
                    "wifiActiveCount": item.wifi_active_count,
                    "wifiActiveMinutes": item.wifi_active_minutes,
                    "menuSales": item.menu_sales,
                    "repeatCustomerCount": item.repeat_customer_count,
                }
                for item in buckets
            ],
        )


class AdminAuditListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get("storeId")
        try:
            store = require_store_access(request.user, store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        logs = AuditLog.objects.filter(store=store).order_by("-created_at")[:500]
        return success(
            request,
            [
                {
                    "auditId": str(item.id),
                    "actorId": item.actor_id,
                    "action": item.action,
                    "resourceType": item.resource_type,
                    "resourceId": item.resource_id,
                    "before": item.before,
                    "after": item.after,
                    "createdAt": item.created_at.isoformat(),
                }
                for item in logs
            ],
        )


class AdminNotificationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get("storeId")
        try:
            store = require_store_access(request.user, store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        notifications = Notification.objects.filter(store=store).order_by("-created_at")[:500]
        return success(
            request,
            [
                {
                    "notificationId": str(item.id),
                    "channel": item.channel,
                    "template": item.template,
                    "destinationLast4": item.destination_last4,
                    "status": item.status,
                    "provider": item.provider,
                    "attempts": item.attempts,
                    "lastError": item.last_error,
                    "createdAt": item.created_at.isoformat(),
                }
                for item in notifications
            ],
        )


class AdminAnomalyListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get("storeId")
        try:
            store = require_store_access(request.user, store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        failed_passes = WiFiPass.objects.filter(
            store=store, status=WiFiPass.Status.FAILED
        ).count()
        failed_notifications = Notification.objects.filter(
            store=store, status=Notification.Status.FAILED
        ).count()
        stale_actions = store.wifi_passes.filter(
            scheduled_actions__completed_at__isnull=True,
            scheduled_actions__execute_at__lt=timezone.now() - timedelta(minutes=5),
        ).distinct().count()
        return success(
            request,
            {
                "failedWifiPasses": failed_passes,
                "failedNotifications": failed_notifications,
                "delayedScheduledActions": stale_actions,
            },
        )


class AdminTeamView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get("storeId")
        try:
            store = require_store_access(request.user, store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        memberships = StoreMembership.objects.filter(store=store).select_related("user")
        return success(
            request,
            [
                {
                    "membershipId": item.id,
                    "userId": str(get_user_public_id(item.user)),
                    "username": item.user.username,
                    "role": item.role,
                }
                for item in memberships
            ],
        )

    def post(self, request):
        serializer = MembershipUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            store = require_store_access(
                request.user, data["store_id"], roles=[StoreMembership.Role.OWNER]
            )
        except PermissionError as exc:
            return _access_error(request, exc)
        try:
            user = UserIdentity.objects.select_related("user").get(
                id=data["user_id"]
            ).user
        except UserIdentity.DoesNotExist:
            return problem_response(
                request=request,
                detail="사용자를 찾을 수 없습니다.",
                code="USER_NOT_FOUND",
                status=404,
            )
        membership, _ = StoreMembership.objects.update_or_create(
            store=store,
            user=user,
            defaults={"role": data["role"]},
        )
        return success(
            request,
            {
                "membershipId": membership.id,
                "userId": str(data["user_id"]),
                "role": membership.role,
            },
            status=201,
        )


class AdminPrivacyRetentionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get("storeId")
        try:
            store = require_store_access(request.user, store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        return success(request, _retention_data(get_or_create_retention_policy(store)))

    def patch(self, request):
        store_id = request.data.get("storeId")
        serializer = PrivacyRetentionSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            store = require_store_access(request.user, store_id, roles=OWNER_MANAGER)
        except PermissionError as exc:
            return _access_error(request, exc)
        policy = get_or_create_retention_policy(store)
        for field, value in serializer.validated_data.items():
            setattr(policy, field, value)
        policy.save()
        return success(request, _retention_data(policy))


def _retention_data(policy):
    return {
        "storeId": str(policy.store_id),
        "phoneRetentionDays": policy.phone_retention_days,
        "verificationRetentionDays": policy.verification_retention_days,
        "auditRetentionDays": policy.audit_retention_days,
        "noticeText": policy.notice_text,
    }


class AdminStoreSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get("storeId")
        try:
            store = require_store_access(request.user, store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        return success(request, _store_settings_data(store))

    def patch(self, request):
        serializer = StoreSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            store = require_store_access(
                request.user, data.pop("store_id"), roles=OWNER_MANAGER
            )
        except PermissionError as exc:
            return _access_error(request, exc)
        before = _store_settings_data(store)
        for field, value in data.items():
            setattr(store, field, value)
        store.save()
        write_audit(
            store=store,
            actor=request.user,
            action="STORE_SETTINGS_UPDATE",
            resource_type="Store",
            resource_id=store.id,
            before=before,
            after=_store_settings_data(store),
        )
        return success(request, _store_settings_data(store))


def _store_settings_data(store):
    return {
        "storeId": str(store.id),
        "name": store.name,
        "timezone": store.timezone,
        "businessDayCutoff": str(store.business_day_cutoff),
        "segment": store.segment,
        "otpSkipEnabled": store.otp_skip_enabled,
    }


class AdminPrivacyExportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store_id = request.query_params.get("storeId")
        try:
            store = require_store_access(
                request.user, store_id, roles=[StoreMembership.Role.OWNER]
            )
        except PermissionError as exc:
            return _access_error(request, exc)
        since = timezone.now() - timedelta(days=30)
        return success(
            request,
            {
                "storeId": str(store.id),
                "generatedAt": timezone.now().isoformat(),
                "periodStart": since.isoformat(),
                "orderEvidence": list(
                    Order.objects.filter(store=store, paid_at__gte=since).values(
                        "external_order_id",
                        "phone_last4",
                        "status",
                        "total_amount",
                        "paid_at",
                    )
                ),
                "verificationEvidence": list(
                    VerificationChallenge.objects.filter(
                        store=store, created_at__gte=since
                    ).values(
                        "phone_last4",
                        "status",
                        "attempts",
                        "created_at",
                        "verified_at",
                    )
                ),
            },
        )


class StoreEventsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, store_id):
        try:
            store = require_store_access(request.user, store_id)
        except PermissionError as exc:
            return _access_error(request, exc)
        last_event_id = request.headers.get("Last-Event-ID")
        queryset = OutboxEvent.objects.filter(store=store).order_by("occurred_at")
        if last_event_id:
            try:
                last = OutboxEvent.objects.get(id=last_event_id, store=store)
                queryset = queryset.filter(occurred_at__gt=last.occurred_at)
            except OutboxEvent.DoesNotExist:
                pass
        events = list(queryset[:100])

        def stream():
            for event in events:
                yield f"id: {event.id}\n"
                yield f"event: {event.type}\n"
                yield f"data: {json.dumps(event.payload, ensure_ascii=False)}\n\n"
            yield ": heartbeat\n\n"

        response = StreamingHttpResponse(stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
