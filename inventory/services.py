from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from ai_ops.models import AIRecommendation
from inventory.models import InventoryEvent, InventoryItem
from operations.services import emit_event


def calculate_risk_score(item: InventoryItem, *, today=None):
    today = today or date.today()
    score = Decimal("0")
    if item.quantity <= item.low_stock_threshold:
        score += Decimal("35")
    if item.expires_on:
        days = (item.expires_on - today).days
        if days < 0:
            score += Decimal("100")
        elif days <= 1:
            score += Decimal("70")
        elif days <= 3:
            score += Decimal("45")
        elif days <= 7:
            score += Decimal("20")
    return min(score, Decimal("100"))


@transaction.atomic
def adjust_inventory(*, item_id, quantity_delta, reason: str):
    item = InventoryItem.objects.select_for_update().select_related("store", "product").get(
        id=item_id
    )
    new_quantity = item.quantity + Decimal(str(quantity_delta))
    if new_quantity < 0:
        raise ValueError("재고 수량은 0보다 작을 수 없습니다.")
    item.quantity = new_quantity
    item.risk_score = calculate_risk_score(item)
    item.save(update_fields=["quantity", "risk_score", "updated_at"])
    InventoryEvent.objects.create(
        item=item,
        type=InventoryEvent.Type.ADJUSTED,
        quantity_delta=quantity_delta,
        reason=reason,
    )
    return item


def scan_inventory_risk(*, store=None, today=None):
    queryset = InventoryItem.objects.select_related("store", "product")
    if store is not None:
        queryset = queryset.filter(store=store)
    recommendations = []
    for item in queryset:
        score = calculate_risk_score(item, today=today)
        if item.risk_score != score:
            item.risk_score = score
            item.save(update_fields=["risk_score", "updated_at"])
        if score >= 45:
            starts_at = timezone.now()
            ends_at = starts_at + timedelta(days=7)
            InventoryEvent.objects.create(
                item=item,
                type=InventoryEvent.Type.RISK_DETECTED,
                reason=f"risk_score={score}",
            )
            recommendation = AIRecommendation.objects.create(
                store=item.store,
                type=AIRecommendation.Type.INVENTORY_PROMOTION,
                payload={
                    "title": f"{item.product.name} 재고 소진 할인",
                    "startsAt": starts_at.isoformat(),
                    "endsAt": ends_at.isoformat(),
                    "productIds": [str(item.product_id)],
                    "discountRate": 15,
                    "source": "INVENTORY_RULE",
                },
                reason=f"{item.product.name} 재고 또는 유통기한 위험이 높습니다.",
                evidence={
                    "quantity": str(item.quantity),
                    "expiresOn": str(item.expires_on) if item.expires_on else None,
                    "riskScore": float(score),
                },
                confidence=Decimal("0.850"),
            )
            recommendations.append(recommendation)
            emit_event(
                store=item.store,
                type="inventory.risk.detected",
                aggregate_type="InventoryItem",
                aggregate_id=item.id,
                payload={"inventoryItemId": str(item.id), "riskScore": float(score)},
            )
    return recommendations
