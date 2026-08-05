import json

from app.services.recommendations import (
    OpenAIRecommendationProvider,
    TimeSaleOutput,
)


def test_openai_provider_uses_responses_structured_output_without_customer_data():
    calls = []

    class FakeResponses:
        def parse(self, **kwargs):
            calls.append(kwargs)
            return type(
                "FakeResponse",
                (),
                {
                    "output_parsed": TimeSaleOutput(
                        title="오후 아메리카노 타임세일",
                        menuIds=["menu-1"],
                        discountRate=15,
                        startsAt="2026-08-05T05:00:00+00:00",
                        endsAt="2026-08-05T07:00:00+00:00",
                        reason="주문이 적은 시간대의 방문을 유도합니다.",
                        confidence=0.91,
                    )
                },
            )()

    class FakeClient:
        responses = FakeResponses()

    provider = OpenAIRecommendationProvider(client_factory=lambda: FakeClient())
    result = provider.generate_time_sale(
        {
            "businessDate": "2026-08-05",
            "timezone": "Asia/Seoul",
            "nowUtc": "2026-08-05T01:00:00+00:00",
            "totalSales": 50000,
            "totalOrders": 10,
            "repeatCustomerCount": 2,
            "wifi": {"activeCount": 3, "activeMinutes": 420},
            "hourly": [
                {
                    "bucketStart": "2026-08-05T05:00:00+00:00",
                    "localHour": 14,
                    "orderCount": 0,
                    "grossSales": 0,
                }
            ],
            "topItems": [{"name": "아메리카노", "quantity": 8}],
            "catalog": [{"menuId": "menu-1", "name": "아메리카노", "price": 5000}],
        }
    )

    assert result.source == "OPENAI"
    assert result.model
    assert result.payload["menuIds"] == ["menu-1"]
    assert calls[0]["store"] is False
    assert calls[0]["text_format"] is TimeSaleOutput
    sent = json.loads(calls[0]["input"][1]["content"])
    serialized = json.dumps(sent, ensure_ascii=False)
    assert "010-1234-5678" not in serialized
    assert "phone" not in serialized.lower()
