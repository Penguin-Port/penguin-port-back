import httpx

from integrations.providers import HttpNetworkAdapter, get_notification_provider, get_trend_provider


def test_http_notification_provider_sends_normalized_payload(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request("POST", url), json={"id": "message-1"})

    monkeypatch.setenv("NOTIFICATION_PROVIDER", "HTTP")
    monkeypatch.setenv("NOTIFICATION_BASE_URL", "https://notify.example/messages")
    monkeypatch.setattr("integrations.providers.httpx.post", fake_post)

    result = get_notification_provider().send_sms(
        destination="01012345678",
        body="인증번호: 123456",
        payload={"challengeId": "challenge-1"},
    )

    assert result.provider == "HTTP"
    assert result.reference == "message-1"
    assert calls[0][1]["json"]["channel"] == "SMS"


def test_http_wifi_adapter_authorize_revoke_and_status(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST":
            return httpx.Response(200, request=httpx.Request(method, url), json={"reference": "ap-session-1", "status": "AUTHORIZED"})
        return httpx.Response(200, request=httpx.Request(method, url), json={"status": "AUTHORIZED"})

    monkeypatch.setenv("WIFI_AP_BASE_URL", "https://wifi.example")
    monkeypatch.setattr("integrations.providers.httpx.request", fake_request)
    adapter = HttpNetworkAdapter()

    authorization = adapter.authorize(pass_id="pass-1", expires_at="2026-08-05T12:00:00Z")
    assert authorization.reference == "ap-session-1"
    assert adapter.get_status(reference=authorization.reference) == "AUTHORIZED"
    assert adapter.revoke(reference=authorization.reference) is True
    assert [item[0] for item in calls] == ["POST", "GET", "DELETE"]


def test_http_trend_provider_normalizes_items(monkeypatch):
    def fake_get(url, **kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={"items": [{"name": "시즌 라떼", "reason": "검색량 증가", "score": 0.9}]},
        )

    monkeypatch.setenv("TREND_PROVIDER", "HTTP")
    monkeypatch.setenv("TREND_API_BASE_URL", "https://trend.example/items")
    monkeypatch.setattr("integrations.providers.httpx.get", fake_get)

    result = get_trend_provider().search(catalog=[])
    assert result[0].name == "시즌 라떼"
    assert result[0].source == "HTTP_TREND_API"
    assert result[0].score == 0.9
