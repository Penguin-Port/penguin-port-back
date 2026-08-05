from datetime import datetime, timedelta
from typing import Any

from app.time import db_now, normalize


BASE_MINUTES = 120
FIRST_ORDER_TIERS = ((10_000, 30), (15_000, 60))
ADDITIONAL_ORDER_TIERS = ((5_000, 60), (10_000, 120))


def _configured_tiers(policy: dict[str, Any] | None, key: str, fallback):
    values = (policy or {}).get(key)
    if not values:
        return fallback
    return tuple((int(item["minAmount"]), int(item["minutes"])) for item in values)


def bonus_minutes(amount: int, tiers: tuple[tuple[int, int], ...]) -> int:
    applicable = [bonus for minimum, bonus in tiers if amount >= minimum]
    return max(applicable, default=0)


def first_order_minutes(amount: int, policy: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    base_minutes = int((policy or {}).get("baseMinutes", BASE_MINUTES))
    tiers = _configured_tiers(policy, "firstOrderTiers", FIRST_ORDER_TIERS)
    bonus = bonus_minutes(amount, tiers)
    total = base_minutes + bonus
    return total, {
        "baseMinutes": base_minutes,
        "orderType": "FIRST",
        "amount": amount,
        "bonusMinutes": bonus,
        "tiers": [{"minAmount": m, "bonusMinutes": b} for m, b in tiers],
    }


def additional_order_minutes(amount: int, policy: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    tiers = _configured_tiers(policy, "additionalOrderTiers", ADDITIONAL_ORDER_TIERS)
    minutes = bonus_minutes(amount, tiers)
    return minutes, {
        "orderType": "ADDITIONAL",
        "amount": amount,
        "extensionMinutes": minutes,
        "tiers": [{"minAmount": m, "extensionMinutes": b} for m, b in tiers],
    }


def business_date(now: datetime) -> datetime.date:
    return normalize(now).date()


def expiry_after(minutes: int, now: datetime | None = None) -> datetime:
    return (normalize(now) if now else db_now()) + timedelta(minutes=minutes)
