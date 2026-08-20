from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AIRecommendation, InventoryEvent, InventoryItem, Product
from app.time import db_now


def calculate_risk_score(item: InventoryItem, *, today: date | None = None) -> int:
    today = today or date.today()
    score = 0
    if item.quantity <= item.low_stock_threshold:
        score += 35
    if item.expires_on:
        days = (item.expires_on - today).days
        if days < 0:
            score += 100
        elif days <= 1:
            score += 70
        elif days <= 3:
            score += 45
        elif days <= 7:
            score += 20
    return min(score, 100)


def inventory_data(db: Session, item: InventoryItem) -> dict:
    product = db.get(Product, item.product_id)
    return {
        "inventoryItemId": item.id,
        "productId": item.product_id,
        "productName": product.name if product else None,
        "quantity": item.quantity,
        "unit": item.unit,
        "lowStockThreshold": item.low_stock_threshold,
        "expiresOn": item.expires_on.isoformat() if item.expires_on else None,
        "riskScore": item.risk_score,
        "updatedAt": item.updated_at.isoformat() if item.updated_at else None,
    }


def scan_inventory(db: Session, *, store_id: str, today: date | None = None) -> list[AIRecommendation]:
    items = db.scalars(select(InventoryItem).where(InventoryItem.store_id == store_id)).all()
    recommendations = []
    for item in items:
        item.risk_score = calculate_risk_score(item, today=today)
        item.updated_at = db_now()
        if item.risk_score < 45:
            continue
        db.add(
            InventoryEvent(
                item_id=item.id,
                type="RISK_DETECTED",
                quantity_delta=0,
                reason=f"risk_score={item.risk_score}",
            )
        )
        pending = next(
            (
                candidate
                for candidate in db.scalars(
                    select(AIRecommendation).where(
                        AIRecommendation.store_id == store_id,
                        AIRecommendation.type == "INVENTORY_PROMOTION",
                        AIRecommendation.status == "PENDING",
                    )
                ).all()
                if candidate.payload.get("inventoryItemId") == item.id
            ),
            None,
        )
        if pending is not None:
            recommendations.append(pending)
            continue
        product = db.get(Product, item.product_id)
        recommendation = AIRecommendation(
            store_id=store_id,
            type="INVENTORY_PROMOTION",
            payload={
                "title": f"{product.name if product else '상품'} 재고 프로모션 추천",
                "inventoryItemId": item.id,
                "productIds": [item.product_id],
                "discountRate": 15,
                "startsAt": db_now().isoformat(),
                "endsAt": db_now().isoformat(),
                "source": "INVENTORY_RULE",
            },
            reason="재고 수량 또는 유통기한 위험이 높습니다.",
            evidence={
                "quantity": item.quantity,
                "expiresOn": item.expires_on.isoformat() if item.expires_on else None,
                "riskScore": item.risk_score,
            },
            confidence=0.85,
        )
        # Ensure the seeded fallback has a valid two-hour approval window.
        from datetime import timedelta

        recommendation.payload = {
            **recommendation.payload,
            "endsAt": (db_now() + timedelta(hours=2)).isoformat(),
        }
        db.add(recommendation)
        db.flush()
        recommendations.append(recommendation)
    db.flush()
    return recommendations
