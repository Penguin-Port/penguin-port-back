# 관리자 이용권 차단 API·SSE 명세

프론트엔드 연결에 필요한 관리자 이용권 차단과 실시간 이벤트 스트림만 정리한 문서입니다.

## 1. 관리자 인증

### Bearer 방식

관리자 로그인 응답의 `data.accessToken`을 사용합니다.

```http
Authorization: Bearer ADMIN_ACCESS_TOKEN
```

### native EventSource 방식

브라우저 기본 `EventSource`는 `Authorization` 헤더를 지정할 수 없으므로 로그인 요청을
`credentials: "include"`로 실행합니다. backend는 `smartpass_access` HttpOnly 쿠키를 발급합니다.

```javascript
await fetch(`${API_URL}/admin/login`, {
  method: "POST",
  credentials: "include",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ username, password }),
});
```

SSE 연결은 다음처럼 실행합니다.

```javascript
const events = new EventSource(`${API_URL}/admin/events`, {
  withCredentials: true,
});
```

교차 사이트 배포에서는 backend에 다음을 설정해야 합니다.

```dotenv
CORS_ALLOWED_ORIGINS=https://배포-프론트-도메인
SECURE_COOKIES=1
```

Bearer 헤더를 지원하는 fetch 기반 SSE 라이브러리를 사용하면 쿠키 대신 다음 헤더를 직접
전달해도 됩니다.

```http
Authorization: Bearer ADMIN_ACCESS_TOKEN
```

## 2. 이용권 차단 API

### 요청

```http
POST /admin/passes/{passId}/block
Content-Type: application/json
Authorization: Bearer ADMIN_ACCESS_TOKEN
```

요청 본문은 생략할 수 있습니다.

```json
{
  "storeId": "STORE_UUID",
  "reason": "시연 중 관리자 차단"
}
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `storeId` | string | 아니오 | 생략 시 관리자 토큰의 매장 사용 |
| `reason` | string | 아니오 | 최대 240자, 기본값 빈 문자열 |

OWNER/MANAGER만 호출할 수 있습니다. 차단 시 Demo Wi-Fi 네트워크 세션을 revoke하고 이용권
상태를 `BLOCKED`로 변경하며 `version`을 1 증가시킵니다. 이미 `BLOCKED`인 이용권에 다시
호출하면 같은 상태를 반환합니다.

### 성공 응답: `200 OK`

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

### 오류 응답

| HTTP | 상황 |
| --- | --- |
| `401` | 관리자 인증 누락·만료·유효하지 않음 |
| `403` | 다른 매장 이용권이거나 STAFF/VIEWER 권한 |
| `404` | `passId`가 존재하지 않음 |
| `409` | `EXPIRED`, `CANCELLED`, `FAILED` 상태에서 차단 시도 |

오류 본문은 backend 공통 problem 형식입니다.

## 3. 관리자 SSE

### 연결

```http
GET /admin/events?storeId=STORE_UUID
Authorization: Bearer ADMIN_ACCESS_TOKEN
Last-Event-ID: EVENT_UUID
```

`storeId`는 선택값이며 생략하면 관리자 토큰의 매장을 사용합니다. 다른 매장을 요청하면
`403`입니다. `Last-Event-ID`가 있으면 해당 이벤트 이후의 저장 이벤트부터 재생합니다.

응답은 다음 헤더를 포함하는 `200 text/event-stream`입니다.

```http
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

초기 연결 시 저장된 이벤트가 먼저 전달되고 `: connected` 주석이 이어집니다. 연결 중에는
새 이벤트와 `: heartbeat` 주석이 전달됩니다. 기본 최대 연결 시간은 300초이며 환경변수
`SSE_MAX_SECONDS`, `SSE_HEARTBEAT_SECONDS`로 조정할 수 있습니다.

### 이벤트 프레임

```text
id: EVENT_UUID
event: wifi.pass.blocked
data: {"passId":"PASS_UUID","status":"BLOCKED","version":2,"reason":"시연 중 관리자 차단"}

```

프론트는 `event`로 분기하고 `data`를 JSON parse합니다. 이벤트 수신 후 이용권 목록을
재조회하면 됩니다.

### MVP 이벤트 목록

| event | `data` 주요 필드 | 발생 시점 |
| --- | --- | --- |
| `order.created` | `orderId`, `externalOrderId`, `totalAmount`, `wifiPassId` | POS 주문 생성 |
| `wifi.pass.activated` | `passId`, `networkReference` | 고객 이용권 활성화 |
| `wifi.pass.extended` | `passId`, `status`, `version`, `expiresAt`, `minutes` | 관리자 연장 |
| `wifi.pass.blocked` | `passId`, `status`, `version`, `reason` | 관리자 차단 |
| `wifi.pass.expired` | `passId`, `status`, `version`, `source` | 관리자 종료·자동 만료 |
| `order.refunded` | `orderId`, `refundAmount`, `status` | POS 환불 |

### 프론트 연결 예시

```javascript
const events = new EventSource(`${API_URL}/admin/events`, {
  withCredentials: true,
});

events.addEventListener("wifi.pass.blocked", (event) => {
  const payload = JSON.parse(event.data);
  refreshPasses(payload.passId);
});

events.addEventListener("wifi.pass.expired", () => {
  refreshActivePasses();
});

events.onerror = () => {
  // EventSource가 자동 재연결합니다.
};
```

전체 주문→OTP→활성화→관리자 조회→추천 승인 흐름은
[`MVP_DEMO_SPEC.md`](MVP_DEMO_SPEC.md)를 참고합니다.
