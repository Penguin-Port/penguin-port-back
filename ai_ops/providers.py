import os
from typing import Protocol

from app.services.recommendations import OpenAIRecommendationProvider, ProviderResult


class AIProvider(Protocol):
    def complete_json(self, *, schema: dict, features: dict) -> dict: ...

    def generate_time_sale(self, features: dict) -> ProviderResult: ...

    def generate_sales_summary(self, features: dict) -> ProviderResult: ...


class RuleBasedAIProvider:
    """외부 AI 장애 또는 미설정 시 핵심 기능을 유지하는 폴백 Provider."""

    def complete_json(self, *, schema: dict, features: dict):
        return {
            "summary": features.get("fallbackSummary", "분석 데이터가 부족합니다."),
            "source": "RULE_FALLBACK",
        }


class OpenAIProvider:
    """Structured-output provider shared with the FastAPI recommendation path."""

    def __init__(self):
        self._provider = OpenAIRecommendationProvider()

    def generate_time_sale(self, features: dict) -> ProviderResult:
        return self._provider.generate_time_sale(features)

    def generate_sales_summary(self, features: dict) -> ProviderResult:
        return self._provider.generate_sales_summary(features)

    def complete_json(self, *, schema: dict, features: dict):
        # Legacy callers receive the safe summary shape; new recommendation
        # callers use the typed methods above.
        result = self.generate_sales_summary(features)
        return {**result.payload, "source": result.source}


def get_ai_provider() -> AIProvider:
    if os.getenv("OPENAI_API_KEY", "").strip():
        return OpenAIProvider()
    return RuleBasedAIProvider()
