from typing import Protocol


class AIProvider(Protocol):
    def complete_json(self, *, schema: dict, features: dict) -> dict: ...


class RuleBasedAIProvider:
    """외부 AI 장애 또는 미설정 시 핵심 기능을 유지하는 폴백 Provider."""

    def complete_json(self, *, schema: dict, features: dict):
        return {
            "summary": features.get("fallbackSummary", "분석 데이터가 부족합니다."),
            "source": "RULE_FALLBACK",
        }


def get_ai_provider() -> AIProvider:
    # OPENAI_API_KEY가 연결되면 JSON Schema 강제 Gateway 구현으로 교체한다.
    return RuleBasedAIProvider()
