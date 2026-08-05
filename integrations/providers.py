from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urljoin

import httpx


class ProviderError(RuntimeError):
    """Raised when an external provider is unavailable or rejects a request."""


@dataclass(frozen=True)
class DeliveryResult:
    provider: str
    reference: str
    status: str = "SENT"


class NotificationProvider(Protocol):
    name: str

    def send_sms(self, *, destination: str, body: str, payload: dict[str, Any]) -> DeliveryResult: ...

    def send_alimtalk(
        self,
        *,
        destination: str,
        body: str,
        payload: dict[str, Any],
    ) -> DeliveryResult: ...


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _timeout(name: str, default: str = "10") -> float:
    try:
        return max(0.5, float(_env(name, default)))
    except ValueError:
        return float(default)


class DemoNotificationProvider:
    name = "DEMO"

    def send_sms(self, *, destination: str, body: str, payload: dict[str, Any]) -> DeliveryResult:
        return DeliveryResult(self.name, f"demo-sms-{secrets.token_hex(8)}")

    def send_alimtalk(
        self,
        *,
        destination: str,
        body: str,
        payload: dict[str, Any],
    ) -> DeliveryResult:
        return DeliveryResult(self.name, f"demo-alimtalk-{secrets.token_hex(8)}")


class SolapiNotificationProvider:
    """Solapi SMS/알림톡 adapter.

    Solapi requires an HMAC-SHA256 authorization header. The adapter only
    receives the destination at the edge of the provider call; callers store
    masked/last-four values in their own database records.
    """

    name = "SOLAPI"

    def __init__(self) -> None:
        self.api_key = _env("SOLAPI_API_KEY")
        self.api_secret = _env("SOLAPI_API_SECRET")
        self.sender = _env("SOLAPI_SENDER")
        self.base_url = _env("SOLAPI_BASE_URL", "https://api.solapi.com")
        self.timeout = _timeout("NOTIFICATION_TIMEOUT_SECONDS")
        if not self.api_key or not self.api_secret or not self.sender:
            raise ProviderError("SOLAPI_API_KEY, SOLAPI_API_SECRET, SOLAPI_SENDER가 필요합니다.")

    def _headers(self) -> dict[str, str]:
        date = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )
        salt = secrets.token_hex(16)
        signature = base64.b64encode(
            hmac.new(
                self.api_secret.encode(),
                f"{date}{salt}".encode(),
                hashlib.sha256,
            ).digest()
        ).decode()
        return {
            "Authorization": (
                f"HMAC-SHA256 apiKey={self.api_key}, date={date}, "
                f"salt={salt}, signature={signature}"
            ),
            "Content-Type": "application/json",
        }

    def _send(self, *, destination: str, text: str, message: dict[str, Any]) -> DeliveryResult:
        payload = {
            "messages": [
                {
                    "to": destination,
                    "from": self.sender,
                    "text": text,
                    **message,
                }
            ]
        }
        try:
            response = httpx.post(
                urljoin(self.base_url.rstrip("/") + "/", "messages/v4/send-many"),
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"Solapi 메시지 발송에 실패했습니다: {exc}") from exc
        reference = data.get("groupId") or data.get("messageId") or "solapi-accepted"
        return DeliveryResult(self.name, str(reference))

    def send_sms(self, *, destination: str, body: str, payload: dict[str, Any]) -> DeliveryResult:
        return self._send(destination=destination, text=body, message={})

    def send_alimtalk(
        self,
        *,
        destination: str,
        body: str,
        payload: dict[str, Any],
    ) -> DeliveryResult:
        kakao_options: dict[str, Any] = {}
        if _env("SOLAPI_KAKAO_PF_ID"):
            kakao_options["pfId"] = _env("SOLAPI_KAKAO_PF_ID")
        if _env("SOLAPI_KAKAO_TEMPLATE_ID"):
            kakao_options["templateId"] = _env("SOLAPI_KAKAO_TEMPLATE_ID")
        variables = payload.get("variables")
        if isinstance(variables, dict):
            kakao_options["variables"] = variables
        message = {"kakaoOptions": kakao_options} if kakao_options else {}
        return self._send(destination=destination, text=body, message=message)


class HttpNotificationProvider:
    """Generic provider hook for a store's approved SMS/알림톡 gateway."""

    name = "HTTP"

    def __init__(self) -> None:
        self.base_url = _env("NOTIFICATION_BASE_URL")
        self.token = _env("NOTIFICATION_API_TOKEN")
        self.timeout = _timeout("NOTIFICATION_TIMEOUT_SECONDS")
        if not self.base_url:
            raise ProviderError("NOTIFICATION_BASE_URL이 필요합니다.")

    def _send(self, *, channel: str, destination: str, body: str, payload: dict[str, Any]) -> DeliveryResult:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request_payload = {
            "channel": channel,
            "to": destination,
            "text": body,
            "payload": payload,
        }
        try:
            response = httpx.post(
                self.base_url,
                headers=headers,
                json=request_payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json() if response.content else {}
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"알림 HTTP 게이트웨이 호출에 실패했습니다: {exc}") from exc
        reference = data.get("reference") or data.get("id") or "http-accepted"
        return DeliveryResult(self.name, str(reference))

    def send_sms(self, *, destination: str, body: str, payload: dict[str, Any]) -> DeliveryResult:
        return self._send(channel="SMS", destination=destination, body=body, payload=payload)

    def send_alimtalk(
        self,
        *,
        destination: str,
        body: str,
        payload: dict[str, Any],
    ) -> DeliveryResult:
        return self._send(channel="ALIMTALK", destination=destination, body=body, payload=payload)


def get_notification_provider() -> NotificationProvider:
    provider = _env("NOTIFICATION_PROVIDER", "DEMO").upper()
    if provider == "SOLAPI":
        return SolapiNotificationProvider()
    if provider == "HTTP":
        return HttpNotificationProvider()
    return DemoNotificationProvider()


@dataclass(frozen=True)
class NetworkAuthorization:
    reference: str
    status: str


class NetworkAdapter(Protocol):
    def authorize(self, *, pass_id: str, expires_at: Any) -> NetworkAuthorization: ...

    def revoke(self, *, reference: str) -> bool: ...

    def get_status(self, *, reference: str) -> str: ...

    def disconnect_device(self, *, reference: str) -> bool: ...


class DemoNetworkAdapter:
    name = "DEMO"

    def authorize(self, *, pass_id: str, expires_at: Any) -> NetworkAuthorization:
        return NetworkAuthorization(reference=f"demo:{pass_id}", status="AUTHORIZED")

    def revoke(self, *, reference: str) -> bool:
        return True

    def get_status(self, *, reference: str) -> str:
        return "AUTHORIZED" if reference else "REVOKED"

    def disconnect_device(self, *, reference: str) -> bool:
        return True


class HttpNetworkAdapter:
    """Generic captive-portal/RADIUS controller adapter.

    The controller contract is intentionally small so a UniFi, MikroTik,
    RADIUS gateway, or an in-house AP controller can be connected without
    changing Wi-Fi business rules.
    """

    name = "HTTP"

    def __init__(self) -> None:
        self.base_url = _env("WIFI_AP_BASE_URL")
        self.token = _env("WIFI_AP_API_TOKEN")
        self.authorize_path = _env("WIFI_AP_AUTHORIZE_PATH", "/v1/sessions")
        self.revoke_path = _env("WIFI_AP_REVOKE_PATH", "/v1/sessions/{reference}")
        self.status_path = _env("WIFI_AP_STATUS_PATH", "/v1/sessions/{reference}")
        self.timeout = _timeout("WIFI_AP_TIMEOUT_SECONDS")
        if not self.base_url:
            raise ProviderError("WIFI_AP_BASE_URL이 필요합니다.")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = httpx.request(
                method,
                urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/")),
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json() if response.content else {}
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"Wi-Fi AP 컨트롤러 호출에 실패했습니다: {exc}") from exc

    def authorize(self, *, pass_id: str, expires_at: Any) -> NetworkAuthorization:
        data = self._request(
            "POST",
            self.authorize_path,
            {"passId": pass_id, "expiresAt": expires_at.isoformat() if hasattr(expires_at, "isoformat") else str(expires_at)},
        )
        reference = data.get("reference") or data.get("sessionId")
        if not reference:
            raise ProviderError("Wi-Fi AP 컨트롤러가 세션 reference를 반환하지 않았습니다.")
        return NetworkAuthorization(str(reference), str(data.get("status", "AUTHORIZED")))

    def revoke(self, *, reference: str) -> bool:
        if not reference:
            return True
        self._request("DELETE", self.revoke_path.format(reference=reference))
        return True

    def get_status(self, *, reference: str) -> str:
        if not reference:
            return "REVOKED"
        data = self._request("GET", self.status_path.format(reference=reference))
        return str(data.get("status", "UNKNOWN"))

    def disconnect_device(self, *, reference: str) -> bool:
        return self.revoke(reference=reference)


def get_network_adapter() -> NetworkAdapter:
    provider = _env("WIFI_NETWORK_PROVIDER", "DEMO").upper()
    if provider == "HTTP":
        return HttpNetworkAdapter()
    return DemoNetworkAdapter()


@dataclass(frozen=True)
class TrendItem:
    name: str
    reason: str
    score: float
    source: str


class TrendProvider(Protocol):
    name: str

    def search(self, *, catalog: list[dict[str, Any]]) -> list[TrendItem]: ...


class DemoTrendProvider:
    name = "FALLBACK_TEMPLATE"

    def search(self, *, catalog: list[dict[str, Any]]) -> list[TrendItem]:
        return [
            TrendItem("말차 디저트", "시즌성 음료·디저트 수요 후보입니다.", 0.50, self.name),
            TrendItem("버터떡", "간편 디저트 카테고리의 신메뉴 후보입니다.", 0.45, self.name),
            TrendItem("시즌 과일 라떼", "계절 과일을 활용한 메뉴 후보입니다.", 0.40, self.name),
        ]


class HttpTrendProvider:
    name = "HTTP_TREND_API"

    def __init__(self) -> None:
        self.base_url = _env("TREND_API_BASE_URL")
        self.token = _env("TREND_API_TOKEN")
        self.timeout = _timeout("TREND_API_TIMEOUT_SECONDS")
        if not self.base_url:
            raise ProviderError("TREND_API_BASE_URL이 필요합니다.")

    def search(self, *, catalog: list[dict[str, Any]]) -> list[TrendItem]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = httpx.get(
                self.base_url,
                headers=headers,
                params={"catalog": ",".join(item.get("name", "") for item in catalog)},
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"외부 트렌드 API 호출에 실패했습니다: {exc}") from exc
        raw_items = data.get("items", data) if isinstance(data, dict) else data
        if not isinstance(raw_items, list):
            raise ProviderError("외부 트렌드 API 응답 형식이 올바르지 않습니다.")
        items = []
        for item in raw_items[:10]:
            if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                continue
            try:
                score = float(item.get("score", 0.0))
            except (TypeError, ValueError):
                score = 0.0
            items.append(
                TrendItem(
                    str(item["name"]).strip(),
                    str(item.get("reason", "외부 트렌드 데이터 기반 후보입니다.")),
                    max(0.0, min(1.0, score)),
                    self.name,
                )
            )
        return items


def get_trend_provider() -> TrendProvider:
    if _env("TREND_PROVIDER", "DEMO").upper() == "HTTP":
        return HttpTrendProvider()
    return DemoTrendProvider()

