# MVP 시연 API·SSE 명세

## 관리자 이용권 차단

### 요청

```http
POST /admin/passes/{passId}/block
Authorization: Bearer ADMIN_ACCESS_TOKEN
Content-Type: application/json
```

본문은 생략할 수 있습니다. 필요하면 매장 검증과 관리자 화면 사유 표시를 위해 다음처럼
전달합니다.

```json
{
  "storeId": "STORE_UUID",
  "reason": "시연 중 관리자 차단"
}
```

`storeId`가 생략되면 로그인한 관리자의 매장을 사용합니다. OWNER/MANAGER만 차단할 수
있으며, 이용권이 다른 매장에 속하면 `403`입니다.

### 성공 응답

```json
{
  "data": {
    "passId": "PASS_UUID",
    "status": "BLOCKED",
    "issuedAt": "2026-08-09T01:00:00+00:00",
    "activatedAt": "2026-08-09T01:05:00+00:00",
    "expiresAt": "2026-08-09T03:05:00+00:00",
    "remainingSeconds": 0,
    "version": 2,
    "policySnapshot": {}
  },
  "meta": {
    "requestId": "req_...",
    "serverTime": "2026-08-09T01:10:00Z"
  }
}
```

차단 시 Demo Wi-Fi 세션을 revoke하고 상태를 `BLOCKED`로 변경하며 `version`을 1 증가시킵니다.
이미 `BLOCKED`인 이용권에 다시 호출하면 같은 상태를 반환하는 멱등 동작입니다. `EXPIRED`,
`CANCELLED`, `FAILED` 이용권은 `409`입니다.

오류 응답은 공통 problem 형식이며 주요 상태 코드는 `401`(관리자 인증 필요), `403`(매장 또는
쓰기 권한 불일치), `404`(이용권 없음), `409`(현재 상태에서 차단 불가)입니다.

## 관리자 SSE

### 인증 방식

두 방식을 모두 지원합니다.

1. fetch 기반 SSE 클라이언트는 `Authorization: Bearer ...` 헤더를 사용합니다.
2. 브라우저 기본 `EventSource`는 로그인/갱신 응답으로 발급된 HttpOnly `smartpass_access`
   쿠키를 사용합니다. 로그인 요청과 EventSource를 `credentials: "include"`/
   `withCredentials: true`로 실행합니다.

```javascript
await fetch(`${API_URL}/admin/login`, {
  method: "POST",
  credentials: "include",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ username, password }),
});

const stream = new EventSource(`${API_URL}/admin/events`, {
  withCredentials: true,
});
stream.addEventListener("wifi.pass.blocked", (event) => {
  const payload = JSON.parse(event.data);
  refreshPasses(payload.passId);
});
```

교차 사이트 배포에서는 backend에 `SECURE_COOKIES=1`과 정확한
`CORS_ALLOWED_ORIGINS=https://배포-프론트-도메인`을 설정해야 합니다.

### 연결

```http
GET /admin/events?storeId=STORE_UUID
Authorization: Bearer ADMIN_ACCESS_TOKEN
Last-Event-ID: EVENT_UUID
Origin: https://배포-프론트-도메인
```

`storeId`는 생략하면 토큰의 매장을 사용합니다. 다른 매장을 요청하면 `403`입니다. 응답은
`200 text/event-stream`이며 `Cache-Control: no-cache`, `Connection: keep-alive`,
`X-Accel-Buffering: no`를 포함합니다.

### 이벤트 프레임

```text
id: EVENT_UUID
event: wifi.pass.blocked
data: {"passId":"PASS_UUID","status":"BLOCKED","version":2,"reason":"시연 중 관리자 차단"}

```

`data`는 JSON payload이며, 프론트는 이벤트 수신 후 해당 조회 API를 재호출하면 됩니다.
초기 연결 시 저장된 이벤트를 먼저 재생하고 `: connected` 주석을 보냅니다. 이후에는 새 이벤트와
`: heartbeat` 주석을 보냅니다. 재연결 시 마지막 `id`를 `Last-Event-ID`로 보내 중복을 줄입니다.

현재 시연에 사용하는 이벤트 타입은 다음과 같습니다.

| event | 주요 payload | 발생 시점 |
| --- | --- | --- |
| `order.created` | `orderId`, `externalOrderId`, `totalAmount`, `wifiPassId` | POS 주문 생성 |
| `wifi.pass.activated` | `passId`, `networkReference` | 고객 이용권 활성화 |
| `wifi.pass.extended` | `passId`, `status`, `version`, `expiresAt`, `minutes` | 관리자 수동 연장 |
| `wifi.pass.blocked` | `passId`, `status`, `version`, `reason` | 관리자 차단 |
| `wifi.pass.expired` | `passId`, `status`, `version`, `source`(자동 만료 시) | 관리자 종료/자동 만료 |
| `order.refunded` | `orderId`, `refundAmount`, `status` | POS 환불 |

## 전체 시연 순서

```text
python -m app.demo_seed
  → POS 주문(X-Demo-Key)
  → order claim 교환
  → OTP send/confirm(데모 코드는 123456)
  → 고객 Portal Session으로 이용권 활성화
  → 관리자 login + SSE 연결
  → active pass 조회 → block API 호출 → wifi.pass.blocked 수신 → 목록 재조회
  → 매출 요약/재고 위험 스캔
  → TIME_SALE 추천 생성 → 필요 시 수정 → 관리자 승인
```

`python -m app.demo_seed`의 출력에는 고객 포털을 시작할 수 있는 `orderClaim.token`,
`demoPhone`, `demoOtpCode`가 포함됩니다. 가장 최근 출력의 Claim으로 다음 주소를 열어
고객 흐름을 시연합니다.

```text
https://penguin-port-customer.pages.dev/connect?orderClaim=ORDER_CLAIM_TOKEN
```

시드 재실행 시 이전 Claim은 교체됩니다. 기본 데모 전화번호는 `010-1234-5678`, OTP는
`123456`입니다.

시연 데이터·OpenAI 배포 변수 등록은 [`docs/DEMO_DEPLOYMENT.md`](DEMO_DEPLOYMENT.md)에
정리되어 있습니다.
