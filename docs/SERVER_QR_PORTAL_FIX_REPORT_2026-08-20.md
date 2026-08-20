# 서버 QR 고객 포털 오류 수정 보고서

작성일: 2026-08-20  
대상 브랜치:

- Backend: `codex/backend-server-risk-fixes`
- Frontend: `codex/frontend-customer-retry-fix`

## 1. 증상

운영 배포 서버에서 POS QR 발급 후 고객용 포털로 진입하면 `/public/order-claims/exchange` 단계에서 500이 발생했습니다.
프론트 화면에서는 CORS/본문 없는 500 응답 영향으로 `Failed to fetch`만 표시되었고, 사용자가 `다시 시도`를 누른 뒤에야 이용권 화면으로 넘어가는 현상이 있었습니다.

또한 관리자 대시보드에서는 활성 이용권 수가 즉시 반영되지 않거나 0장처럼 보이는 문제가 보고되었습니다.

## 2. 직접 원인

운영 PostgreSQL의 `DateTime(timezone=True)` 컬럼은 timezone-aware `datetime`으로 반환될 수 있습니다.
반면 백엔드의 `db_now()`는 SQLite 호환을 위해 UTC timezone-naive 값을 반환합니다.

이 상태에서 아래와 같은 비교가 수행되면 Python에서 `TypeError`가 발생합니다.

```python
claim.expires_at <= db_now()
```

로컬 SQLite에서는 대부분 naive 값으로 반환되어 정상처럼 보였지만, 운영 PostgreSQL에서는 aware/naive 비교 충돌이 발생한 것이 핵심 차이입니다.

## 3. 수정 내용

### 3.1 datetime normalize 적용

시간 비교 전 `app.time.normalize()`를 사용해 timezone-aware 값을 UTC-naive로 맞춘 뒤 비교하도록 수정했습니다.

적용 위치:

- `/public/order-claims/exchange`
- `/public/otp/send`
- `/public/otp/confirm`
- `/public/passes/{pass_id}/activate`
- `/public/coupons`
- `/public/coupons/{coupon_id}/redeem`
- `/admin/refresh`
- `/admin/passes/{pass_id}/extend`
- POS 주문 이용권 연장 계산

### 3.2 QR / OTP / 이용권 / 리워드 동시성 방어

운영 PostgreSQL에서 실제 동시 요청이 들어올 때 중복 처리될 수 있는 지점에 행 잠금을 추가했습니다.

적용 위치:

- QR claim exchange: `OrderClaim.with_for_update()`
- OTP confirm: `OtpChallenge.with_for_update()`
- 이용권 activate/extend/expire/block: `WiFiPass.with_for_update()`
- 리워드 choose: `RewardGrant.with_for_update()`
- POS refund: `Order.with_for_update()`

### 3.3 POS/idempotency unique 충돌 처리

POS 주문/환불 커밋 시 `IntegrityError`가 발생하면 무조건 500으로 터지지 않도록 처리했습니다.

- 같은 idempotency key + 같은 request hash면 기존 응답 replay
- 다른 요청 또는 unique 충돌이면 409 반환

### 3.4 외부 Wi-Fi provider 실패 처리

이용권 activate/expire/block 시 외부 provider 호출 실패가 발생하면, 상태 변경을 성공처럼 커밋하지 않고 502로 반환하도록 보강했습니다.

### 3.5 전역 500 응답 관측성 개선

예상하지 못한 예외도 problem JSON 형태로 반환되도록 전역 exception handler를 추가했습니다.
이로써 프론트에서 단순 `Failed to fetch`만 보는 상황을 줄이고, `requestId`가 있는 에러 본문을 받을 수 있게 했습니다.

### 3.6 PostgreSQL 문자열 길이 차이 방어

SQLite는 문자열 길이 제한을 느슨하게 처리하지만 PostgreSQL은 `VARCHAR(n)`을 엄격히 검사합니다.
요청 schema에 `max_length`를 추가해 DB 진입 전에 422로 차단되도록 했습니다.

적용 예:

- `memberId`: DB `customer_key String(160)` 기준으로 prefix를 고려해 153자로 제한
- `externalOrderId`: 120자 제한
- inventory unit/reason 길이 제한

### 3.7 JSON in-place 수정 미반영 위험 수정

SQLAlchemy JSON 컬럼은 dict 내부만 직접 바꾸면 변경 감지가 안 될 수 있습니다.
`recommendation.payload["endsAt"] = ...` 형태를 새 dict 할당 방식으로 수정했습니다.

## 4. 프론트 수정 내용

고객 포털의 `/connect?orderClaim=...` exchange 실패 시 `orderClaim`을 즉시 지우지 않도록 수정했습니다.

기존에는 에러 화면 진입 과정에서 claim이 제거될 수 있어 `다시 시도`가 실제 exchange 재시도가 아니라 잘못된 화면 이동이 될 위험이 있었습니다.

수정 후:

- exchange 실패 시 `orderClaim` 보존
- `다시 시도` 클릭 시 exchange API를 다시 호출
- 성공 시 이전 에러 상태 초기화

단, 서버가 이미 claim을 성공적으로 소비했는데 네트워크 응답만 유실되는 극단 케이스에서는 두 번째 exchange가 409가 될 수 있습니다.
이 경우까지 완전 방어하려면 exchange endpoint를 verification ticket 재발급 가능 구조로 idempotent하게 확장하는 별도 설계가 필요합니다.

## 5. 로컬 실제 데이터 이동 검증

새 코드 기준으로 로컬 서버 3개를 재시작했습니다.

- Backend: `http://127.0.0.1:8000`
- Admin frontend: `http://127.0.0.1:5173`
- Customer frontend: `http://127.0.0.1:5174`

API로 실제 주문을 생성하고 고객 포털 인증/활성화/관리자 조회까지 검증했습니다.

검증 데이터:

- Store ID: `bc811031-9d06-4643-9491-78c14e179528`
- Product ID: `fa8e95cc-c9ed-4950-abcd-05591116dcaa`
- Order ID: `31fb578a-0321-4705-a5d6-bf46d2b36e5a`
- Pass ID: `44d8f655-b928-4c91-a0b5-20e11f18bebf`
- Activated status: `ACTIVE`
- Remaining seconds: `7200`

DB 최종 상태:

- `orders.status = PAID`
- `order_claims.exchanged_at IS NOT NULL`
- `otp_challenges.status = VERIFIED`
- `wifi_passes.status = ACTIVE`
- 이벤트 순서:
  1. `order.created`
  2. `order.claim.exchanged`
  3. `wifi.pass.activated`

관리자 API `/admin/passes/active` 응답에도 해당 pass가 포함됨을 확인했습니다.

## 6. 검증 결과

Backend:

```text
.venv\Scripts\python.exe -m pytest
40 passed
```

Frontend:

```text
npm run typecheck
passed
```

빌드 검증:

기존 `dist` 폴더가 Windows 권한/잠금 문제로 일반 `npm run build`의 출력 디렉터리 정리 단계에서 EPERM이 발생했습니다.
코드 번들 자체 확인을 위해 임시 outDir로 `vite build`를 실행했고 성공했습니다.

```text
vite build --outDir <temp> --emptyOutDir=true
built successfully
```

## 7. 배포 전 확인 필요 사항

팀장 또는 배포 담당자가 운영에서 확인해야 할 항목입니다.

1. 운영 PostgreSQL에서 `/public/order-claims/exchange` 500 재발 여부
2. 해당 endpoint의 500 로그가 `TypeError: can't compare offset-naive and offset-aware datetimes`에서 사라졌는지
3. Cloudflare Pages 고객 도메인에서 CORS preflight/에러 응답 본문이 정상 반환되는지
4. QR 진입 후 첫 시도에서 OTP 화면 또는 이용권 화면으로 정상 이동하는지
5. 고객 이용권 활성화 직후 관리자 `/admin/passes/active`에 동일 pass가 노출되는지
6. 외부 Wi-Fi provider 장애 시 pass 상태가 `ACTIVE`로 잘못 커밋되지 않는지

