from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Sum
from django.db.models.functions import TruncHour
from django.utils import timezone

from ai_ops.models import AIRecommendation, AnalyticsHourly, Promotion
from operations.services import emit_event
from orders.models import Order


@transaction.atomic
def accept_recommendation(
    *,
    recommendation_id,
    store_id,
    expected_version: int,
    title: str,
    payload: dict,
    starts_at,
    ends_at,
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
    if ends_at <= starts_at:
        raise ValueError("프로모션 종료 시각은 시작 시각 이후여야 합니다.")

    recommendation.status = AIRecommendation.Status.ACCEPTED
    recommendation.payload = payload
    recommendation.version += 1
    recommendation.decided_at = timezone.now()
    recommendation.save(
        update_fields=["status", "payload", "version", "decided_at"]
    )
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
    return AIRecommendation.objects.create(
        store=store,
        type=AIRecommendation.Type.TIME_SALE,
        payload={
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
    suggestions = ["말차 디저트", "버터떡", "시즌 과일 라떼"]
    return [
        AIRecommendation.objects.create(
            store=store,
            type=AIRecommendation.Type.MENU_TREND,
            payload={"menuName": name, "source": "FALLBACK_TEMPLATE"},
            reason="외부 트렌드 API 미연결 시 사용하는 인기 카테고리 템플릿입니다.",
            evidence={"source": "curated_fallback"},
            confidence=Decimal("0.500"),
        )
        for name in suggestions
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
