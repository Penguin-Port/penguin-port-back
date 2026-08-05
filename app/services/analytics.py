from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AIRecommendation,
    AnalyticsHourly,
    InventoryItem,
    Order,
    OrderItem,
    Product,
    WiFiPass,
)
from app.time import aware, db_now


def _zone(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Seoul")


def _bucket(value: datetime, timezone_name: str) -> datetime:
    current = aware(value).astimezone(_zone(timezone_name))
    return current.replace(minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def sales_summary(db: Session, *, store, business_date: date) -> dict:
    orders = db.scalars(
        select(Order).where(
            Order.store_id == store.id,
            Order.business_date == business_date,
            Order.status.in_(["PAID", "PARTIALLY_REFUNDED"]),
        )
    ).all()
    order_ids = [order.id for order in orders]
    items = (
        db.scalars(select(OrderItem).where(OrderItem.order_id.in_(order_ids))).all()
        if order_ids
        else []
    )
    products = {
        product.id: product.name
        for product in db.scalars(select(Product).where(Product.store_id == store.id)).all()
    }
    hourly = defaultdict(lambda: {"orderCount": 0, "grossSales": 0})
    customer_orders = Counter(order.customer_key for order in orders)
    top_items = Counter()
    for order in orders:
        key = _bucket(order.paid_at, store.timezone).isoformat()
        hourly[key]["orderCount"] += 1
        hourly[key]["grossSales"] += max(0, order.total_amount - order.refunded_amount)
    for item in items:
        top_items[products.get(item.product_id, item.name_snapshot)] += item.quantity

    active_passes = db.scalars(
        select(WiFiPass).where(
            WiFiPass.store_id == store.id,
            WiFiPass.business_date == business_date,
            WiFiPass.status.in_(["ACTIVE", "EXPIRING_SOON"]),
        )
    ).all()
    repeat_count = sum(1 for count in customer_orders.values() if count > 1)
    summary = {
        "businessDate": business_date.isoformat(),
        "totalSales": sum(max(0, order.total_amount - order.refunded_amount) for order in orders),
        "totalOrders": len(orders),
        "repeatCustomerCount": repeat_count,
        "wifiActiveCount": len(active_passes),
        "wifiActiveMinutes": sum(
            max(0, int((aware(item.expires_at) - aware(item.activated_at or item.issued_at)).total_seconds() / 60))
            for item in active_passes
        ),
        "hourly": [
            {"bucketStart": key, **value} for key, value in sorted(hourly.items())
        ],
        "topItems": [
            {"name": name, "quantity": quantity}
            for name, quantity in top_items.most_common(10)
        ],
    }
    for key, value in hourly.items():
        bucket_start = datetime.fromisoformat(key)
        row = db.scalar(
            select(AnalyticsHourly).where(
                AnalyticsHourly.store_id == store.id,
                AnalyticsHourly.bucket_start == bucket_start,
            )
        )
        if row is None:
            row = AnalyticsHourly(store_id=store.id, bucket_start=bucket_start)
            db.add(row)
        row.order_count = value["orderCount"]
        row.gross_sales = value["grossSales"]
        row.wifi_active_count = len(active_passes)
        row.wifi_active_minutes = summary["wifiActiveMinutes"]
        row.menu_sales = dict(top_items)
        row.repeat_customer_count = repeat_count
        row.generated_at = db_now()
    db.flush()
    return summary


def get_or_create_sales_recommendation(db: Session, *, store, summary: dict) -> AIRecommendation:
    existing = db.scalars(
        select(AIRecommendation)
        .where(
            AIRecommendation.store_id == store.id,
            AIRecommendation.type == "SALES_SUMMARY",
        )
        .order_by(AIRecommendation.created_at.desc())
    ).first()
    if existing is not None and existing.payload.get("businessDate") == summary["businessDate"]:
        return existing
    quiet = min(summary["hourly"], key=lambda item: item["orderCount"], default=None)
    text = (
        f"{quiet['bucketStart']} 전후 주문이 가장 적습니다. 타임세일 후보 시간대로 검토해 보세요."
        if quiet
        else "분석할 주문 데이터가 아직 없습니다."
    )
    recommendation = AIRecommendation(
        store_id=store.id,
        type="SALES_SUMMARY",
        payload={"businessDate": summary["businessDate"], "summary": text},
        reason="시간대별 주문·매출 집계를 규칙 기반으로 요약했습니다.",
        evidence={
            "totalSales": summary["totalSales"],
            "totalOrders": summary["totalOrders"],
            "quietBucket": quiet,
        },
        confidence=0.8,
    )
    db.add(recommendation)
    db.flush()
    return recommendation
