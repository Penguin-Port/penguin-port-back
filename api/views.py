import hashlib
import json
import uuid

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from ai_ops.services import accept_recommendation
from api.access import require_store_access
from api.auth import create_portal_session, read_portal_session
from api.exceptions import problem_response
from api.permissions import PosHMACPermission
from api.serializers import (
    OrderClaimExchangeSerializer,
    PaidOrderInputSerializer,
    ImmediateBenefitApplySerializer,
    RecommendationAcceptSerializer,
    RewardChooseSerializer,
)
from identity.services import create_verification_ticket
from orders.models import IdempotencyRecord, OrderClaim
from orders.services import create_paid_order
from rewards.models import (
    Coupon,
    ImmediateBenefitRedemption,
    RewardGrant,
    RewardTierBenefit,
)
from rewards.services import (
    apply_immediate_redemption,
    choose_benefit,
    get_upsell_hint,
)
from stores.models import Store
from wifi.models import WiFiPass
from wifi.services import activate_pass


def success(request, data, status=200):
    return Response(
        {
            "data": data,
            "meta": {
                "requestId": request.headers.get(
                    "X-Request-Id", f"req_{uuid.uuid4().hex}"
                ),
                "serverTime": timezone.now().isoformat().replace("+00:00", "Z"),
            },
        },
        status=status,
    )


def pass_data(wifi_pass, breakdown=None):
    data = {
        "passId": str(wifi_pass.id),
        "status": wifi_pass.status,
        "businessDate": wifi_pass.business_date.isoformat(),
        "issuedAt": wifi_pass.issued_at.isoformat(),
        "activatedAt": (
            wifi_pass.activated_at.isoformat() if wifi_pass.activated_at else None
        ),
        "expiresAt": wifi_pass.expires_at.isoformat(),
        "policyVersion": wifi_pass.policy_version,
        "passVersion": wifi_pass.pass_version,
    }
    if breakdown is not None:
        data["breakdown"] = breakdown
    return data


class PosOrderCreateView(APIView):
    authentication_classes = []
    permission_classes = [PosHMACPermission]
    throttle_scope = "pos"

    def post(self, request):
        serializer = PaidOrderInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        idempotency_key = request.headers.get("Idempotency-Key")
        if not idempotency_key:
            return problem_response(
                request=request,
                detail="Idempotency-Key 헤더가 필요합니다.",
                code="IDEMPOTENCY_KEY_REQUIRED",
                status=400,
            )
        request_hash = hashlib.sha256(
            json.dumps(request.data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        try:
            store = Store.objects.get(id=data["store_id"])
            existing = IdempotencyRecord.objects.filter(
                store=store, key=idempotency_key
            ).first()
            if existing:
                if existing.request_hash != request_hash:
                    return problem_response(
                        request=request,
                        detail="같은 Idempotency-Key가 다른 요청에 사용되었습니다.",
                        code="IDEMPOTENCY_KEY_CONFLICT",
                        status=409,
                    )
                return Response(existing.response_body, status=existing.response_status)
            customer = data["customer"]
            result = create_paid_order(
                store=store,
                external_order_id=data["external_order_id"],
                member_id=customer.get("member_id"),
                phone=customer.get("phone"),
                items=data["items"],
                total_amount=data["total_amount"],
                paid_at=data["paid_at"],
            )
        except Store.DoesNotExist:
            return problem_response(
                request=request,
                detail="매장을 찾을 수 없습니다.",
                code="STORE_NOT_FOUND",
                status=404,
            )
        except IntegrityError:
            return problem_response(
                request=request,
                detail="이미 처리된 externalOrderId입니다.",
                code="DUPLICATE_ORDER",
                status=409,
            )
        except ValueError as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="ORDER_RULE_VIOLATION",
                status=422,
            )

        response = success(
            request,
            {
                "orderId": str(result.order.id),
                "businessDate": result.order.business_date.isoformat(),
                "dailyTotal": result.reward.daily_total,
                "newRewardGrantIds": [
                    str(grant.id) for grant in result.reward.new_grants
                ],
                "wifiPass": pass_data(
                    result.wifi.wifi_pass, breakdown=result.wifi.breakdown
                ),
                "orderClaim": {
                    "token": result.order_claim_token,
                    "expiresAt": result.order_claim_expires_at.isoformat(),
                },
            },
            status=201,
        )
        IdempotencyRecord.objects.create(
            store=store,
            key=idempotency_key,
            request_hash=request_hash,
            response_body=response.data,
            response_status=response.status_code,
        )
        return response


class PosImmediateBenefitApplyView(APIView):
    authentication_classes = []
    permission_classes = [PosHMACPermission]
    throttle_scope = "pos"

    def post(self, request, redemption_id):
        serializer = ImmediateBenefitApplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            redemption = apply_immediate_redemption(
                redemption_id=redemption_id,
                **serializer.validated_data,
            )
        except ImmediateBenefitRedemption.DoesNotExist:
            return problem_response(
                request=request,
                detail="즉시 혜택을 찾을 수 없습니다.",
                code="IMMEDIATE_BENEFIT_NOT_FOUND",
                status=404,
            )
        except ValueError as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="IMMEDIATE_BENEFIT_NOT_APPLICABLE",
                status=409,
            )
        return success(
            request,
            {
                "redemptionId": str(redemption.id),
                "status": redemption.status,
                "orderId": str(redemption.applied_order_id),
                "benefit": redemption.benefit_snapshot,
                "appliedAt": redemption.applied_at.isoformat(),
            },
        )


class OrderClaimExchangeView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_scope = "public"

    @transaction.atomic
    def post(self, request):
        serializer = OrderClaimExchangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token_hash = hashlib.sha256(
            serializer.validated_data["order_claim"].encode()
        ).hexdigest()
        try:
            claim = (
                OrderClaim.objects.select_for_update()
                .select_related("order")
                .get(token_hash=token_hash)
            )
        except OrderClaim.DoesNotExist:
            return problem_response(
                request=request,
                detail="주문 Claim이 유효하지 않습니다.",
                code="ORDER_CLAIM_INVALID",
                status=404,
            )
        if claim.exchanged_at is not None:
            return problem_response(
                request=request,
                detail="이미 사용된 주문 Claim입니다.",
                code="ORDER_CLAIM_ALREADY_EXCHANGED",
                status=409,
            )
        if claim.expires_at <= timezone.now():
            return problem_response(
                request=request,
                detail="주문 Claim이 만료되었습니다.",
                code="ORDER_CLAIM_EXPIRED",
                status=410,
            )
        claim.exchanged_at = timezone.now()
        claim.save(update_fields=["exchanged_at"])
        wifi_pass = WiFiPass.objects.get(
            store=claim.order.store,
            customer_key=claim.order.customer_key,
            business_date=claim.order.business_date,
        )
        otp_skipped = (
            claim.order.store.otp_skip_enabled
            and claim.order.customer_key.startswith("member:")
        )
        portal_session = (
            create_portal_session(
                customer_key=claim.order.customer_key,
                store_id=str(claim.order.store_id),
            )
            if otp_skipped
            else None
        )
        return success(
            request,
            {
                "verificationTicket": (
                    None if otp_skipped else create_verification_ticket(claim.order)
                ),
                "portalSession": portal_session,
                "requiresVerification": not otp_skipped,
                "passId": str(wifi_pass.id),
                "expiresIn": 86400 if otp_skipped else 600,
            },
        )


class PassDetailView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, pass_id):
        try:
            session = read_portal_session(request)
            wifi_pass = WiFiPass.objects.get(
                id=pass_id,
                customer_key=session["customerKey"],
                store_id=session["storeId"],
            )
        except PermissionError as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="PORTAL_SESSION_INVALID",
                status=401,
            )
        except WiFiPass.DoesNotExist:
            return problem_response(
                request=request,
                detail="이용권을 찾을 수 없습니다.",
                code="WIFI_PASS_NOT_FOUND",
                status=404,
            )
        data = pass_data(wifi_pass)
        progress = get_upsell_hint(
            store_id=wifi_pass.store_id,
            customer_key=wifi_pass.customer_key,
        )
        data["rewardProgress"] = progress
        data["availableCouponCount"] = Coupon.objects.filter(
            store=wifi_pass.store,
            customer_key=wifi_pass.customer_key,
            status=Coupon.Status.AVAILABLE,
            expires_at__gt=timezone.now(),
        ).count()
        return success(request, data)


class PassActivateView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, pass_id):
        try:
            session = read_portal_session(request)
            wifi_pass = activate_pass(
                pass_id=pass_id, customer_key=session["customerKey"]
            )
        except PermissionError as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="PORTAL_SESSION_INVALID",
                status=401,
            )
        except WiFiPass.DoesNotExist:
            return problem_response(
                request=request,
                detail="이용권을 찾을 수 없습니다.",
                code="WIFI_PASS_NOT_FOUND",
                status=404,
            )
        except ValueError as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="WIFI_PASS_INVALID_TRANSITION",
                status=409,
            )
        return success(request, pass_data(wifi_pass))


class UpsellHintView(APIView):
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
        data = get_upsell_hint(
            store_id=session["storeId"], customer_key=session["customerKey"]
        )
        data["suggestedItems"] = []
        return success(request, data)


class RewardOptionsView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, grant_id):
        try:
            session = read_portal_session(request)
            grant = RewardGrant.objects.select_related("tier").get(
                id=grant_id,
                store_id=session["storeId"],
                customer_key=session["customerKey"],
            )
        except PermissionError as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="PORTAL_SESSION_INVALID",
                status=401,
            )
        except RewardGrant.DoesNotExist:
            return problem_response(
                request=request,
                detail="리워드 지급 건을 찾을 수 없습니다.",
                code="REWARD_GRANT_NOT_FOUND",
                status=404,
            )
        options = [
            {
                "benefitId": str(benefit.id),
                "type": benefit.benefit_type,
                "title": benefit.title,
                "payload": benefit.payload,
                "recommended": index == 0,
            }
            for index, benefit in enumerate(grant.tier.benefits.filter(is_active=True))
        ]
        return success(
            request,
            {
                "grantId": str(grant.id),
                "tierAmount": grant.tier.threshold_amount,
                "status": grant.status,
                "options": options,
            },
        )


class RewardChooseView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, grant_id):
        serializer = RewardChooseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            session = read_portal_session(request)
            grant, coupon, immediate_redemption = choose_benefit(
                grant_id=grant_id,
                customer_key=session["customerKey"],
                **serializer.validated_data,
            )
        except PermissionError as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="PORTAL_SESSION_INVALID",
                status=401,
            )
        except (RewardGrant.DoesNotExist, RewardTierBenefit.DoesNotExist):
            return problem_response(
                request=request,
                detail="리워드 지급 건을 찾을 수 없습니다.",
                code="REWARD_GRANT_NOT_FOUND",
                status=404,
            )
        except ValueError as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="REWARD_ALREADY_CHOSEN",
                status=409,
            )
        return success(
            request,
            {
                "grantId": str(grant.id),
                "status": grant.status,
                "fulfillMode": grant.fulfill_mode,
                "coupon": (
                    {
                        "couponId": str(coupon.id),
                        "status": coupon.status,
                        "expiresAt": coupon.expires_at.isoformat(),
                    }
                    if coupon
                    else None
                ),
                "immediateRedemption": (
                    {
                        "redemptionId": str(immediate_redemption.id),
                        "status": immediate_redemption.status,
                        "benefit": immediate_redemption.benefit_snapshot,
                        "expiresAt": immediate_redemption.expires_at.isoformat(),
                    }
                    if immediate_redemption
                    else None
                ),
            },
        )


class RecommendationAcceptView(APIView):
    def post(self, request, recommendation_id):
        serializer = RecommendationAcceptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            require_store_access(
                request.user,
                serializer.validated_data["store_id"],
                roles=["OWNER", "MANAGER"],
            )
            promotion = accept_recommendation(
                recommendation_id=recommendation_id,
                expected_version=serializer.validated_data.pop("version"),
                **serializer.validated_data,
            )
        except PermissionError as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="STORE_ACCESS_DENIED",
                status=403,
            )
        except ValueError as exc:
            return problem_response(
                request=request,
                detail=str(exc),
                code="RECOMMENDATION_VERSION_CONFLICT",
                status=409,
            )
        return success(
            request,
            {
                "promotionId": str(promotion.id),
                "status": promotion.status,
                "startsAt": promotion.starts_at.isoformat(),
                "endsAt": promotion.ends_at.isoformat(),
            },
            status=201,
        )
