from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.celery_app import celery_app
from app.db import session_scope
from app.models import Promotion, Store
from app.services.analytics import sales_summary
from app.services.recommendations import (
    generate_menu_trend_recommendations,
    generate_sales_summary_recommendation,
    generate_time_sale_recommendation,
)
from app.services.wifi import expire_due_passes
from app.services.privacy import purge_sensitive_data
from app.time import business_date


def _task(func):
    if celery_app is None:  # pragma: no cover - prevents import-time crashes
        return func
    return celery_app.task(name=f"app.tasks.{func.__name__}")(func)


@_task
def run_minutely_jobs() -> dict:
    with session_scope() as db:
        expired = expire_due_passes(db)
        purged = purge_sensitive_data(db)
        now = datetime.now(timezone.utc)
        started = db.query(Promotion).filter(
            Promotion.status == "SCHEDULED",
            Promotion.starts_at <= now,
            Promotion.ends_at > now,
        ).update({Promotion.status: "ACTIVE"}, synchronize_session=False)
        ended = db.query(Promotion).filter(
            Promotion.status.in_(["SCHEDULED", "ACTIVE"]),
            Promotion.ends_at <= now,
        ).update({Promotion.status: "ENDED"}, synchronize_session=False)
        return {
            "expiredPasses": expired,
            "purgedRows": purged,
            "promotionsStarted": started,
            "promotionsEnded": ended,
        }


@_task
def run_hourly_jobs() -> dict:
    result: dict[str, dict] = {}
    with session_scope() as db:
        for store in db.scalars(select(Store)).all():
            target_date = business_date(
                datetime.now(timezone.utc),
                timezone_name=store.timezone,
                cutoff=store.business_day_cutoff,
            )
            summary = sales_summary(db, store=store, business_date=target_date)
            time_sale = generate_time_sale_recommendation(
                db, store=store, business_date=target_date
            )
            result[store.id] = {
                "timeSaleRecommendationId": time_sale.id,
                "totalOrders": summary["totalOrders"],
            }
    return result


@_task
def run_daily_jobs() -> dict:
    result: dict[str, dict] = {}
    with session_scope() as db:
        for store in db.scalars(select(Store)).all():
            target_date = business_date(
                datetime.now(timezone.utc),
                timezone_name=store.timezone,
                cutoff=store.business_day_cutoff,
            )
            sales = generate_sales_summary_recommendation(
                db, store=store, business_date=target_date
            )
            menu_trends = generate_menu_trend_recommendations(db, store=store)
            result[store.id] = {
                "salesSummaryRecommendationId": sales.id,
                "menuTrendRecommendationIds": [item.id for item in menu_trends],
            }
    return result

