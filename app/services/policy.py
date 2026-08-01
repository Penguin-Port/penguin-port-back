from datetime import datetime, timedelta
from typing import Any

from app.time import db_now, normalize


BASE_MINUTES = 120
FIRST_ORDER_TIERS = ((10_000, 30), (15_000, 60))
ADDITIONAL_ORDER_TIERS = ((5_000, 60), (10_000, 120))


def bonus_minutes(amount: int, tiers: tuple[tuple[int, int], ...]) -> int:
    applicable = [bonus for minimum, bonus in tiers if amount >= minimum]
    return max(applicable, default=0)


def first_order_minutes(amount: int) -> tuple[int, dict[str, Any]]:
    bonus = bonus_minutes(amount, FIRST_ORDER_TIERS)
    total = BASE_MINUTES + bonus
    return total, {
        "baseMinutes": BASE_MINUTES,
        "orderType": "FIRST",
        "amount": amount,
        "bonusMinutes": bonus,
        "tiers": [{"minAmount": m, "bonusMinutes": b} for m, b in FIRST_ORDER_TIERS],
    }


def additional_order_minutes(amount: int) -> tuple[int, dict[str, Any]]:
    minutes = bonus_minutes(amount, ADDITIONAL_ORDER_TIERS)
    return minutes, {
        "orderType": "ADDITIONAL",
        "amount": amount,
        "extensionMinutes": minutes,
        "tiers": [{"minAmount": m, "extensionMinutes": b} for m, b in ADDITIONAL_ORDER_TIERS],
    }


def business_date(now: datetime) -> datetime.date:
    return normalize(now).date()


def expiry_after(minutes: int, now: datetime | None = None) -> datetime:
    return (normalize(now) if now else db_now()) + timedelta(minutes=minutes)
