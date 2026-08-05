import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AIRecommendation, InventoryItem, Product
from app.services.analytics import sales_summary


logger = logging.getLogger(__name__)


class TimeSaleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    title: str = Field(min_length=1, max_length=160)
    menu_ids: list[str] = Field(alias="menuIds", min_length=1, max_length=5)
    discount_rate: int = Field(alias="discountRate", ge=1, le=100)
    starts_at: str = Field(alias="startsAt", min_length=1, max_length=80)
    ends_at: str = Field(alias="endsAt", min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)


class SalesSummaryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)


@dataclass(frozen=True)
class ProviderResult:
    payload: dict[str, Any]
    reason: str
    evidence: dict[str, Any]
    confidence: float
    source: str
    model: str | None = None


class RecommendationProvider(Protocol):
    def generate_time_sale(self, features: dict[str, Any]) -> ProviderResult: ...

    def generate_sales_summary(self, features: dict[str, Any]) -> ProviderResult: ...


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Seoul")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("시간에는 timezone offset이 필요합니다.")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def build_ai_features(
    db: Session,
    *,
    store,
    summary: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build only aggregate/catalog features safe to send to the model.

    Order IDs, customer keys, phone numbers, claims, OTPs, and tokens are
    intentionally not read or included here.
    """

    now = now or datetime.now(timezone.utc)
    zone = _zone(store.timezone)
    hourly = []
    for item in summary.get("hourly", []):
        try:
            bucket = _parse_utc(item["bucketStart"])
        except (KeyError, TypeError, ValueError):
            continue
        local = bucket.astimezone(zone)
        hourly.append(
            {
                "bucketStart": _iso(bucket),
                "localDate": local.date().isoformat(),
                "localHour": local.hour,
                "orderCount": int(item.get("orderCount", 0)),
                "grossSales": int(item.get("grossSales", 0)),
            }
        )

    products = db.scalars(
        select(Product)
        .where(Product.store_id == store.id, Product.is_active.is_(True))
        .order_by(Product.name, Product.id)
    ).all()
    inventory = db.scalars(
        select(InventoryItem).where(InventoryItem.store_id == store.id)
    ).all()
    inventory_by_product = {item.product_id: item for item in inventory}
    catalog = [
        {
            "menuId": product.id,
            "name": product.name,
            "price": product.price,
            "inventory": (
                {
                    "quantity": inventory_by_product[product.id].quantity,
                    "riskScore": inventory_by_product[product.id].risk_score,
                }
                if product.id in inventory_by_product
                else None
            ),
        }
        for product in products
    ]
    return {
        "businessDate": summary["businessDate"],
        "timezone": store.timezone,
        "nowUtc": _iso(now),
        "totalSales": int(summary.get("totalSales", 0)),
        "totalOrders": int(summary.get("totalOrders", 0)),
        "repeatCustomerCount": int(summary.get("repeatCustomerCount", 0)),
        "wifi": {
            "activeCount": int(summary.get("wifiActiveCount", 0)),
            "activeMinutes": int(summary.get("wifiActiveMinutes", 0)),
        },
        "hourly": hourly,
        "topItems": summary.get("topItems", []),
        "catalog": catalog,
    }


def _quiet_bucket(features: dict[str, Any]) -> dict[str, Any] | None:
    hourly = features.get("hourly") or []
    return min(
        hourly,
        key=lambda item: (int(item.get("orderCount", 0)), int(item.get("grossSales", 0))),
        default=None,
    )


def _fallback_window(features: dict[str, Any]) -> tuple[datetime, datetime]:
    now = _parse_utc(features["nowUtc"])
    quiet = _quiet_bucket(features)
    start = now + timedelta(hours=1)
    if quiet:
        try:
            candidate = _parse_utc(quiet["bucketStart"])
            if candidate > now:
                start = candidate
            else:
                start = candidate + timedelta(days=1)
        except (KeyError, TypeError, ValueError):
            pass
    start = start.replace(minute=0, second=0, microsecond=0)
    if start <= now:
        start = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return start, start + timedelta(hours=2)


class RuleBasedRecommendationProvider:
    def generate_time_sale(self, features: dict[str, Any]) -> ProviderResult:
        start, end = _fallback_window(features)
        catalog = features.get("catalog") or []
        selected = catalog[0] if catalog else None
        menu_ids = [selected["menuId"]] if selected else []
        menu_name = selected["name"] if selected else "추천 메뉴"
        quiet = _quiet_bucket(features)
        reason = (
            f"{quiet['localHour']}시 주문이 가장 적어 방문을 유도하는 "
            "타임세일입니다."
            if quiet
            else (
                "분석 데이터가 부족하여 기본 타임세일 후보를 생성했습니다."
            )
        )
        return ProviderResult(
            payload={
                "title": (
                    f"{start.astimezone(_zone(features['timezone'])).hour}시 "
                    f"{menu_name} 타임세일 추천"
                ),
                "menuIds": menu_ids,
                "discountRate": min(15, settings.promotion_max_discount_rate),
                "startsAt": _iso(start),
                "endsAt": _iso(end),
            },
            reason=reason,
            evidence={"quietBucket": quiet, "selectedMenuId": menu_ids[0] if menu_ids else None},
            confidence=0.75 if quiet else 0.45,
            source="RULE_FALLBACK",
        )

    def generate_sales_summary(self, features: dict[str, Any]) -> ProviderResult:
        quiet = _quiet_bucket(features)
        if quiet:
            summary = (
                f"{quiet['localHour']}시 전후 주문이 가장 적어 타임세일 후보 "
                "시간대로 검토할 수 있습니다."
            )
            reason = "시간대별 주문·매출 집계를 규칙 기반으로 요약했습니다."
        else:
            summary = "분석할 주문 데이터가 아직 없습니다."
            reason = (
                "분석 데이터가 부족하여 규칙 기반 "
                "기본 요약을 반환했습니다."
            )
        return ProviderResult(
            payload={"summary": summary},
            reason=reason,
            evidence={"quietBucket": quiet},
            confidence=0.8 if quiet else 0.4,
            source="RULE_FALLBACK",
        )


TIME_SALE_INSTRUCTIONS = """You are a cafe operations analyst.
Return exactly one safe TIME_SALE recommendation using the supplied aggregate data.
Use only menuIds that exist in catalog. The recommendation is not published or
accepted automatically. Do not change Wi-Fi policy, rewards, prices, or customer
identity rules. Discount and time-window limits are enforced by the application.
Return startsAt and endsAt as timezone-aware ISO 8601 timestamps in UTC, and choose
a future two-hour window informed by the quietest local hour when possible.
"""

SALES_SUMMARY_INSTRUCTIONS = """You are a cafe operations analyst.
Summarize the supplied aggregate sales and Wi-Fi metrics in one concise Korean
sentence and explain the evidence. Do not infer or mention any customer identity,
phone number, order ID, claim, OTP, token, or other personal data. This is an
analysis card only; do not publish a promotion or change business policy.
"""


OutputModel = TypeVar("OutputModel", bound=BaseModel)


def _default_openai_client():
    from openai import OpenAI

    kwargs: dict[str, Any] = {
        "api_key": settings.openai_api_key,
        "timeout": settings.openai_timeout_seconds,
    }
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return OpenAI(**kwargs)


class OpenAIRecommendationProvider:
    def __init__(self, *, client_factory=None):
        self._client_factory = client_factory or _default_openai_client

    def _parse(
        self,
        *,
        instructions: str,
        features: dict[str, Any],
        output_model: type[OutputModel],
    ):
        client = self._client_factory()
        response = client.responses.parse(
            model=settings.openai_model,
            input=[
                {"role": "system", "content": instructions},
                {
                    "role": "user",
                    "content": json.dumps(features, ensure_ascii=False, sort_keys=True),
                },
            ],
            text_format=output_model,
            store=False,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise ValueError("OpenAI 응답에 구조화된 추천 결과가 없습니다.")
        return parsed

    def generate_time_sale(self, features: dict[str, Any]) -> ProviderResult:
        parsed = self._parse(
            instructions=TIME_SALE_INSTRUCTIONS,
            features=features,
            output_model=TimeSaleOutput,
        )
        return ProviderResult(
            payload={
                "title": parsed.title.strip(),
                "menuIds": list(parsed.menu_ids),
                "discountRate": parsed.discount_rate,
                "startsAt": parsed.starts_at,
                "endsAt": parsed.ends_at,
            },
            reason=parsed.reason.strip(),
            evidence={},
            confidence=parsed.confidence,
            source="OPENAI",
            model=settings.openai_model,
        )

    def generate_sales_summary(self, features: dict[str, Any]) -> ProviderResult:
        parsed = self._parse(
            instructions=SALES_SUMMARY_INSTRUCTIONS,
            features=features,
            output_model=SalesSummaryOutput,
        )
        return ProviderResult(
            payload={"summary": parsed.summary.strip()},
            reason=parsed.reason.strip(),
            evidence={},
            confidence=parsed.confidence,
            source="OPENAI",
            model=settings.openai_model,
        )


def get_ai_provider() -> RecommendationProvider:
    if settings.openai_api_key:
        return OpenAIRecommendationProvider()
    return RuleBasedRecommendationProvider()


def _validate_time_sale_payload(
    payload: dict[str, Any],
    *,
    features: dict[str, Any],
) -> dict[str, Any]:
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
        raise ValueError("추천 제목이 유효하지 않습니다.")
    menu_ids = payload.get("menuIds")
    allowed_menu_ids = {item["menuId"] for item in features.get("catalog", [])}
    if allowed_menu_ids and (
        not isinstance(menu_ids, list)
        or not menu_ids
        or len(menu_ids) != len(set(menu_ids))
        or any(menu_id not in allowed_menu_ids for menu_id in menu_ids)
    ):
        raise ValueError(
            "추천 메뉴가 현재 매장 카탈로그와 일치하지 않습니다."
        )
    if not isinstance(menu_ids, list):
        menu_ids = []
    try:
        discount_rate = int(payload["discountRate"])
        starts_at = _parse_utc(payload["startsAt"])
        ends_at = _parse_utc(payload["endsAt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("추천 할인율 또는 시간이 유효하지 않습니다.") from exc
    if not 0 <= discount_rate <= settings.promotion_max_discount_rate:
        raise ValueError("추천 할인율이 정책 한도를 초과했습니다.")
    now = _parse_utc(features["nowUtc"])
    if starts_at <= now or ends_at <= starts_at:
        raise ValueError(
            "추천 프로모션 시간은 미래의 유효한 구간이어야 합니다."
        )
    if ends_at - starts_at > timedelta(hours=settings.promotion_max_duration_hours):
        raise ValueError(
            "추천 프로모션 적용 시간이 정책 한도를 초과했습니다."
        )
    return {
        "title": title.strip(),
        "menuIds": menu_ids,
        "discountRate": discount_rate,
        "startsAt": _iso(starts_at),
        "endsAt": _iso(ends_at),
    }


def _base_evidence(features: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
    evidence = {
        "businessDate": features["businessDate"],
        "totalSales": features["totalSales"],
        "totalOrders": features["totalOrders"],
        "quietBucket": _quiet_bucket(features),
        "provider": result.source,
    }
    if result.model:
        evidence["model"] = result.model
    evidence.update(result.evidence)
    return evidence


def _safe_provider_result(
    *,
    provider: RecommendationProvider,
    fallback: RuleBasedRecommendationProvider,
    method: str,
    features: dict[str, Any],
) -> ProviderResult:
    try:
        result = getattr(provider, method)(features)
        if method == "generate_time_sale":
            result = ProviderResult(
                payload=_validate_time_sale_payload(result.payload, features=features),
                reason=result.reason,
                evidence=result.evidence,
                confidence=result.confidence,
                source=result.source,
                model=result.model,
            )
        return result
    except Exception as exc:  # provider/network/schema failures must not break the demo
        logger.warning("AI recommendation fallback: %s", type(exc).__name__)
        result = getattr(fallback, method)(features)
        return ProviderResult(
            payload=result.payload,
            reason=result.reason,
            evidence={**result.evidence, "fallbackReason": "OPENAI_UNAVAILABLE_OR_INVALID"},
            confidence=result.confidence,
            source=result.source,
            model=None,
        )


def generate_time_sale_recommendation(
    db: Session,
    *,
    store,
    business_date: date,
    provider: RecommendationProvider | None = None,
) -> AIRecommendation:
    summary = sales_summary(db, store=store, business_date=business_date)
    features = build_ai_features(db, store=store, summary=summary)
    fallback = RuleBasedRecommendationProvider()
    result = _safe_provider_result(
        provider=provider or get_ai_provider(),
        fallback=fallback,
        method="generate_time_sale",
        features=features,
    )
    payload = {
        **result.payload,
        "businessDate": business_date.isoformat(),
        "source": result.source,
    }
    if result.model:
        payload["model"] = result.model
    recommendation = AIRecommendation(
        store_id=store.id,
        type="TIME_SALE",
        payload=payload,
        reason=result.reason,
        evidence=_base_evidence(features, result),
        confidence=max(0.0, min(1.0, float(result.confidence))),
    )
    db.add(recommendation)
    db.flush()
    return recommendation


def generate_sales_summary_recommendation(
    db: Session,
    *,
    store,
    business_date: date,
    provider: RecommendationProvider | None = None,
) -> AIRecommendation:
    summary = sales_summary(db, store=store, business_date=business_date)
    features = build_ai_features(db, store=store, summary=summary)
    fallback = RuleBasedRecommendationProvider()
    result = _safe_provider_result(
        provider=provider or get_ai_provider(),
        fallback=fallback,
        method="generate_sales_summary",
        features=features,
    )
    payload = {
        **result.payload,
        "businessDate": business_date.isoformat(),
        "source": result.source,
    }
    if result.model:
        payload["model"] = result.model
    recommendation = AIRecommendation(
        store_id=store.id,
        type="SALES_SUMMARY",
        payload=payload,
        reason=result.reason,
        evidence=_base_evidence(features, result),
        confidence=max(0.0, min(1.0, float(result.confidence))),
    )
    db.add(recommendation)
    db.flush()
    return recommendation


def generate_menu_trend_recommendations(db: Session, *, store) -> list[AIRecommendation]:
    from integrations.providers import DemoTrendProvider, ProviderError, get_trend_provider

    catalog = [
        {"menuId": item.id, "name": item.name, "price": item.price}
        for item in db.scalars(
            select(Product).where(Product.store_id == store.id, Product.is_active.is_(True))
        ).all()
    ]
    try:
        trends = get_trend_provider().search(catalog=catalog)
    except ProviderError as exc:
        logger.warning("Trend provider fallback: %s", type(exc).__name__)
        trends = DemoTrendProvider().search(catalog=catalog)

    recommendations = []
    for trend in trends:
        recommendation = AIRecommendation(
            store_id=store.id,
            type="MENU_TREND",
            payload={"menuName": trend.name, "source": trend.source},
            reason=trend.reason,
            evidence={"provider": trend.source, "catalogSize": len(catalog)},
            confidence=trend.score,
        )
        db.add(recommendation)
        db.flush()
        recommendations.append(recommendation)
    return recommendations
