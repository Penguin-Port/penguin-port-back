from datetime import timedelta, timezone as datetime_timezone
from decimal import Decimal
import os

from django.db import transaction
from django.db.models import Count, Sum
from django.db.models.functions import TruncHour
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ai_ops.models import AIRecommendation, AnalyticsHourly, Promotion
from operations.services import emit_event
from orders.models import Order
from catalog.models import Product
from integrations.providers import DemoTrendProvider, ProviderError, get_trend_provider
from ai_ops.providers import get_ai_provider
from wifi.models import WiFiPass


def validate_promotion_payload(*, store, recommendation_type: str, payload: dict) -> dict:
    """Validate the editable fields before a recommendation can publish."""

    if not isinstance(payload, dict):
        raise ValueError("추천 payload는 객체여야 합니다.")
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
        raise ValueError("추천 제목이 유효하지 않습니다.")

    normalized = {**payload, "title": title.strip()}
    menu_ids = payload.get("menuIds")
    if recommendation_type == AIRecommendation.Type.TIME_SALE:
        if menu_ids is not None:
            if not isinstance(menu_ids, list):
                raise ValueError("타임세일 메뉴 목록이 유효하지 않습니다.")
            menu_ids = [str(item) for item in menu_ids]
            if not menu_ids:
                fallback_product = Product.objects.filter(store=store, is_active=True).first()
                if fallback_product is None:
                    raise ValueError("타임세일 메뉴를 하나 이상 선택해야 합니다.")
                menu_ids = [str(fallback_product.id)]
            products = Product.objects.filter(
                store=store,
                id__in=menu_ids,
                is_active=True,
            )
            if products.count() != len(set(menu_ids)):
                raise ValueError("추천 메뉴가 해당 매장 카탈로그와 일치하지 않습니다.")
            normalized["menuIds"] = menu_ids

    try:
        discount_rate = int(payload.get("discountRate", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("추천 할인율이 유효하지 않습니다.") from exc
    if not 0 <= discount_rate <= int(os.getenv("PROMOTION_MAX_DISCOUNT_RATE", "50")):
        raise ValueError("추천 할인율이 정책 한도를 초과했습니다.")
    normalized["discountRate"] = discount_rate

    starts_at = parse_datetime(str(payload.get("startsAt", "")))
    ends_at = parse_datetime(str(payload.get("endsAt", "")))
    if (
        starts_at is None
        or ends_at is None
        or not timezone.is_aware(starts_at)
        or not timezone.is_aware(ends_at)
    ):
        raise ValueError("추천에 유효한 startsAt, endsAt이 필요합니다.")
    if ends_at <= starts_at:
        raise ValueError("프로모션 종료 시각은 시작 시각 이후여야 합니다.")
    max_hours = int(os.getenv("PROMOTION_MAX_DURATION_HOURS", "24"))
    if ends_at - starts_at > timedelta(hours=max_hours):
        raise ValueError("프로모션 적용 시간이 정책 한도를 초과했습니다.")
    normalized["startsAt"] = starts_at.isoformat()
    normalized["endsAt"] = ends_at.isoformat()
    return normalized


@transaction.atomic
def accept_recommendation(
    *,
    recommendation_id,
    store_id,
    expected_version: int,
) -> Promotion:
    recommendation = AIRecommendation.objects.select_for_update().get(
        id=recommendation_id,
        store_id=store_id,
    )
    if recommendation.version != expected_version:
        raise ValueError("추천이 다른 사용자에 의해 변경되었습니다.")
    if recommendation.status not in [
        AIRecommendation.Status.PENDING,
        AIRecommendation.Status.EDITED,
    ]:
        raise ValueError("대기 또는 수정 상태의 추천만 승인할 수 있습니다.")

    payload = validate_promotion_payload(
        store=recommendation.store,
        recommendation_type=recommendation.type,
        payload=recommendation.payload,
    )
    title = payload["title"]
    starts_at = parse_datetime(payload["startsAt"])
    ends_at = parse_datetime(payload["endsAt"])

    recommendation.status = AIRecommendation.Status.ACCEPTED
    recommendation.version += 1
    recommendation.decided_at = timezone.now()
    recommendation.save(update_fields=["status", "version", "decided_at"])
    promotion = Promotion.objects.create(
        store_id=store_id,
        source_recommendation=recommendation,
        title=title,
        payload=payload,
        starts_at=starts_at,
        ends_at=ends_at,
    )
    emit_event(
        store=recommendation.store,
        type="promotion.scheduled",
        aggregate_type="Promotion",
        aggregate_id=promotion.id,
        payload={
            "promotionId": str(promotion.id),
            "startsAt": starts_at.isoformat(),
            "endsAt": ends_at.isoformat(),
        },
    )
    return promotion


def aggregate_hourly_sales(*, store, start=None, end=None):
    end = end or timezone.now()
    start = start or end - timedelta(days=1)
    rows = (
        Order.objects.filter(
            store=store,
            status=Order.Status.PAID,
            paid_at__gte=start,
            paid_at__lt=end,
        )
        .annotate(bucket=TruncHour("paid_at"))
        .values("bucket")
        .annotate(order_count=Count("id"), gross_sales=Sum("total_amount"))
        .order_by("bucket")
    )
    results = []
    for row in rows:
        analytics, _ = AnalyticsHourly.objects.update_or_create(
            store=store,
            bucket_start=row["bucket"],
            defaults={
                "order_count": row["order_count"],
                "gross_sales": row["gross_sales"] or 0,
            },
        )
        results.append(analytics)
    return results


def _build_ai_features(*, store, buckets):
    now = timezone.now()
    products = list(
        Product.objects.filter(store=store, is_active=True).order_by("name", "id")
    )
    active_passes = WiFiPass.objects.filter(
        store=store,
        status__in=[WiFiPass.Status.ACTIVE, WiFiPass.Status.EXPIRING_SOON],
    )
    return {
        "businessDate": now.astimezone().date().isoformat(),
        "timezone": store.timezone,
        "nowUtc": now.astimezone(datetime_timezone.utc).isoformat(),
        "totalSales": sum(bucket.gross_sales for bucket in buckets),
        "totalOrders": sum(bucket.order_count for bucket in buckets),
        "repeatCustomerCount": 0,
        "wifi": {"activeCount": active_passes.count(), "activeMinutes": 0},
        "hourly": [
            {
                "bucketStart": bucket.bucket_start.astimezone(datetime_timezone.utc).isoformat(),
                "localHour": bucket.bucket_start.astimezone().hour,
                "orderCount": bucket.order_count,
                "grossSales": bucket.gross_sales,
            }
            for bucket in buckets
        ],
        "topItems": [],
        "catalog": [
            {"menuId": str(product.id), "name": product.name, "price": product.price}
            for product in products
        ],
    }


def generate_sales_summary(*, store):
    buckets = list(
        AnalyticsHourly.objects.filter(store=store)
        .order_by("-bucket_start")[:24]
    )
    if not buckets:
        aggregate_hourly_sales(store=store)
        buckets = list(
            AnalyticsHourly.objects.filter(store=store)
            .order_by("-bucket_start")[:24]
        )
    total_sales = sum(bucket.gross_sales for bucket in buckets)
    total_orders = sum(bucket.order_count for bucket in buckets)
    if buckets:
        quiet = min(buckets, key=lambda bucket: bucket.order_count)
        summary = (
            f"{quiet.bucket_start.astimezone().strftime('%H시')} 전후 주문이 가장 적습니다. "
            "타임세일 후보 시간대로 검토해 보세요."
        )
    else:
        quiet = None
        summary = "분석할 주문 데이터가 아직 없습니다."
    features = _build_ai_features(store=store, buckets=buckets)
    provider = get_ai_provider()
    if hasattr(provider, "generate_sales_summary"):
        try:
            candidate = provider.generate_sales_summary(features)
            candidate_summary = candidate.payload.get("summary")
            if isinstance(candidate_summary, str) and candidate_summary.strip():
                return AIRecommendation.objects.create(
                    store=store,
                    type=AIRecommendation.Type.SALES_SUMMARY,
                    payload={
                        "summary": candidate_summary.strip(),
                        "source": candidate.source,
                        **({"model": candidate.model} if candidate.model else {}),
                    },
                    reason=candidate.reason,
                    evidence={
                        "provider": candidate.source,
                        "totalSales": features["totalSales"],
                        "totalOrders": features["totalOrders"],
                        "quietBucket": quiet.bucket_start.isoformat() if quiet else None,
                    },
                    confidence=Decimal(str(max(0.0, min(1.0, candidate.confidence)))),
                )
        except Exception:
            pass
    return AIRecommendation.objects.create(
        store=store,
        type=AIRecommendation.Type.SALES_SUMMARY,
        payload={"summary": summary},
        reason="시간대별 주문·매출 집계를 규칙 기반으로 요약했습니다.",
        evidence={
            "totalSales": total_sales,
            "totalOrders": total_orders,
            "quietBucket": quiet.bucket_start.isoformat() if quiet else None,
        },
        confidence=Decimal("0.800"),
    )


def generate_time_sale_recommendation(*, store):
    buckets = list(
        AnalyticsHourly.objects.filter(store=store, order_count__gte=0)
        .order_by("order_count", "gross_sales")[:1]
    )
    if not buckets:
        aggregate_hourly_sales(store=store)
        buckets = list(
            AnalyticsHourly.objects.filter(store=store).order_by("order_count")[:1]
        )
    quiet = buckets[0] if buckets else None
    now = timezone.now()
    starts_at = quiet.bucket_start if quiet else now + timedelta(hours=1)
    if starts_at <= now:
        starts_at += timedelta(days=(now - starts_at).days + 1)
    ends_at = starts_at + timedelta(minutes=120)
    features = _build_ai_features(store=store, buckets=buckets)
    provider = get_ai_provider()
    ai_result = None
    if hasattr(provider, "generate_time_sale"):
        try:
            candidate = provider.generate_time_sale(features)
            candidate_menu_ids = candidate.payload.get("menuIds") or []
            allowed_menu_ids = {item["menuId"] for item in features["catalog"]}
            candidate_start = parse_datetime(candidate.payload.get("startsAt", ""))
            candidate_end = parse_datetime(candidate.payload.get("endsAt", ""))
            if (
                isinstance(candidate.payload.get("title"), str)
                and candidate_menu_ids
                and set(candidate_menu_ids).issubset(allowed_menu_ids)
                and 1 <= int(candidate.payload.get("discountRate", 0)) <= 50
                and candidate_start is not None
                and candidate_end is not None
                and timezone.is_aware(candidate_start)
                and timezone.is_aware(candidate_end)
                and candidate_end > candidate_start
                and candidate_start > now
            ):
                ai_result = candidate
                starts_at, ends_at = candidate_start, candidate_end
        except Exception:
            ai_result = None
    if ai_result is not None:
        payload = {
            **ai_result.payload,
            "source": ai_result.source,
            **({"model": ai_result.model} if ai_result.model else {}),
        }
        return AIRecommendation.objects.create(
            store=store,
            type=AIRecommendation.Type.TIME_SALE,
            payload=payload,
            reason=ai_result.reason,
            evidence={
                "provider": ai_result.source,
                "totalSales": features["totalSales"],
                "totalOrders": features["totalOrders"],
                "quietBucket": quiet.bucket_start.isoformat() if quiet else None,
            },
            confidence=Decimal(str(max(0.0, min(1.0, ai_result.confidence)))),
        )
    return AIRecommendation.objects.create(
        store=store,
        type=AIRecommendation.Type.TIME_SALE,
        payload={
            "title": "타임세일 15% 할인",
            "startsAt": starts_at.isoformat(),
            "endsAt": ends_at.isoformat(),
            "windowStart": quiet.bucket_start.isoformat() if quiet else None,
            "durationMinutes": 120,
            "discountRate": 15,
            "menuIds": [],
            "source": "RULE_FALLBACK",
        },
        reason="주문이 적은 시간대의 방문을 유도하는 타임세일입니다.",
        evidence={
            "orderCount": quiet.order_count if quiet else 0,
            "grossSales": quiet.gross_sales if quiet else 0,
        },
        confidence=Decimal("0.750"),
    )


def generate_menu_trend_fallback(*, store):
    catalog = [
        {"menuId": str(item.id), "name": item.name, "price": item.price}
        for item in Product.objects.filter(store=store, is_active=True)
    ]
    try:
        suggestions = get_trend_provider().search(catalog=catalog)
    except ProviderError:
        suggestions = DemoTrendProvider().search(catalog=catalog)
    return [
        AIRecommendation.objects.create(
            store=store,
            type=AIRecommendation.Type.MENU_TREND,
            payload={"menuName": item.name, "source": item.source},
            reason=item.reason,
            evidence={"source": item.source, "catalogSize": len(catalog)},
            confidence=Decimal(str(item.score)),
        )
        for item in suggestions
    ]


@transaction.atomic
def reject_recommendation(*, recommendation_id, store_id, reason=""):
    recommendation = AIRecommendation.objects.select_for_update().get(
        id=recommendation_id, store_id=store_id
    )
    if recommendation.status not in [
        AIRecommendation.Status.PENDING,
        AIRecommendation.Status.EDITED,
    ]:
        raise ValueError("대기 또는 수정 상태의 추천만 거절할 수 있습니다.")
    recommendation.status = AIRecommendation.Status.REJECTED
    recommendation.payload = {**recommendation.payload, "rejectionReason": reason}
    recommendation.version += 1
    recommendation.decided_at = timezone.now()
    recommendation.save(
        update_fields=["status", "payload", "version", "decided_at"]
    )
    return recommendation


def update_promotion_states(*, now=None):
    now = now or timezone.now()
    started = Promotion.objects.filter(
        status=Promotion.Status.SCHEDULED,
        starts_at__lte=now,
        ends_at__gt=now,
    ).update(status=Promotion.Status.ACTIVE)
    ended = Promotion.objects.filter(
        status__in=[Promotion.Status.SCHEDULED, Promotion.Status.ACTIVE],
        ends_at__lte=now,
    ).update(status=Promotion.Status.ENDED)
    return {"started": started, "ended": ended}
