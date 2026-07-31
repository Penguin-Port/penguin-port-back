from ai_ops.services import (
    aggregate_hourly_sales,
    generate_menu_trend_fallback,
    generate_sales_summary,
    generate_time_sale_recommendation,
    update_promotion_states,
)
from inventory.services import scan_inventory_risk
from operations.services import purge_expired_privacy_data
from stores.models import Store
from wifi.workers import process_due_actions


def run_minutely_jobs():
    return {
        "scheduledActions": process_due_actions(),
        "promotions": update_promotion_states(),
    }


def run_hourly_jobs():
    result = {}
    for store in Store.objects.all():
        aggregate_hourly_sales(store=store)
        result[str(store.id)] = {
            "timeSaleRecommendationId": str(
                generate_time_sale_recommendation(store=store).id
            ),
            "inventoryRecommendationIds": [
                str(item.id) for item in scan_inventory_risk(store=store)
            ],
        }
    return result


def run_daily_jobs():
    result = {}
    for store in Store.objects.all():
        summary = generate_sales_summary(store=store)
        trends = generate_menu_trend_fallback(store=store)
        result[str(store.id)] = {
            "salesSummaryId": str(summary.id),
            "menuTrendIds": [str(item.id) for item in trends],
        }
    result["purgedPrivacyRows"] = purge_expired_privacy_data()
    return result
