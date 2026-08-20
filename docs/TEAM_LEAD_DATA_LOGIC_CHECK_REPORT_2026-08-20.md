# 팀장 확인용 데이터/로직 과정 보고서

작성일: 2026-08-20  
목적: QR 고객 포털 진입 오류 수정 후, 팀장이 운영 데이터 흐름과 확인 포인트를 검토할 수 있도록 정리

## 1. 핵심 데이터 흐름

### 1단계. POS 주문 생성

Endpoint:

```text
POST /pos/orders
```

생성/갱신 데이터:

- `orders`
- `order_items`
- `order_claims`
- `wifi_passes`
- `daily_spend_balances`
- `reward_grants`
- `backend_events`
- `idempotency_keys`

확인 포인트:

- `orders.external_order_id`가 중복이면 409가 나야 합니다.
- 같은 `Idempotency-Key`와 같은 요청 본문이면 기존 응답이 replay되어야 합니다.
- 같은 고객/매장/영업일에 이미 이용권이 있으면 새 이용권을 만들지 않고 기존 이용권 시간을 연장해야 합니다.
- `wifi_passes.expires_at` 계산 시 `normalize()`가 적용되어 aware/naive 충돌이 없어야 합니다.

## 2. QR claim 교환

Endpoint:

```text
POST /public/order-claims/exchange
```

입력:

- QR URL의 `orderClaim` token

주요 처리:

1. token hash로 `order_claims` 조회
2. `FOR UPDATE`로 claim 행 잠금
3. `expires_at`을 `normalize()` 후 현재 시간과 비교
4. `exchanged_at` 기록
5. verification ticket 발급
6. 주문/상품/이용권 정보를 고객 포털에 반환

확인 포인트:

- 운영 PostgreSQL에서 `TypeError` 500이 없어야 합니다.
- 정상 응답은 `verificationTicket`, `passId`, `storeId`, `items`, `providedMinutes`를 포함해야 합니다.
- 이미 사용된 claim은 409로 반환되어야 합니다.
- 만료 claim은 410으로 반환되어야 합니다.

주의:

- 현재 구조는 claim을 한 번 소비합니다.
- 서버는 성공 처리했지만 클라이언트가 응답을 못 받은 극단 케이스에서는 재시도 시 409가 날 수 있습니다.
- 이 케이스까지 제품적으로 보장하려면 claim exchange를 idempotent하게 재설계해야 합니다.

## 3. OTP 발송

Endpoint:

```text
POST /public/otp/send
```

생성 데이터:

- `otp_challenges`
- `demo_messages` 또는 외부 provider 발송 기록

확인 포인트:

- provider 초기화/발송 실패가 500이 아니라 502로 반환되어야 합니다.
- challenge의 `expires_at` 비교가 aware/naive 충돌 없이 동작해야 합니다.
- 데모 provider에서는 `demoCode`가 응답에 포함됩니다.

## 4. OTP 확인

Endpoint:

```text
POST /public/otp/confirm
```

주요 처리:

1. `otp_challenges`를 `FOR UPDATE`로 잠금
2. 만료 시간 normalize 비교
3. OTP code hash 검증
4. challenge status를 `VERIFIED`로 변경
5. portal session 발급
6. 고객 이용권 `passId` 반환

확인 포인트:

- 동시에 같은 challenge를 confirm해도 중복 인증 상태가 꼬이지 않아야 합니다.
- 실패 횟수 초과/만료/이미 사용 상태는 409 또는 410 계열로 반환되어야 합니다.

## 5. 이용권 활성화

Endpoint:

```text
POST /public/passes/{pass_id}/activate
```

주요 처리:

1. portal session의 `storeId`, `customerKey`와 pass 소유자 일치 확인
2. `wifi_passes`를 `FOR UPDATE`로 잠금
3. status가 `ISSUED` 또는 `ACTIVATING`인지 확인
4. 만료 시간 normalize 비교
5. Wi-Fi provider authorize 호출
6. 성공 시 `status = ACTIVE`, `activated_at`, `network_reference` 저장
7. `wifi.pass.activated` 이벤트 발행

확인 포인트:

- provider 실패 시 이용권이 `ACTIVE`로 커밋되지 않아야 합니다.
- 성공 후 `/public/passes/{pass_id}`에서 `ACTIVE`와 잔여 시간이 보여야 합니다.
- 성공 후 `/admin/passes/active`에도 같은 pass가 보여야 합니다.

## 6. 관리자 활성 이용권 조회

Endpoint:

```text
GET /admin/passes/active
```

응답 구조:

```json
{
  "data": [
    {
      "passId": "...",
      "status": "ACTIVE",
      "remainingSeconds": 7200
    }
  ]
}
```

확인 포인트:

- 프론트는 `data.length` 기준으로 활성 이용권 수를 계산합니다.
- `data.items` 구조가 아니므로 QA 스크립트나 운영 점검 쿼리 작성 시 혼동하지 않아야 합니다.
- 조회 직전 `expire_due_passes()`가 실행되므로 만료된 pass는 자동으로 제외될 수 있습니다.

## 7. 리워드 선택/쿠폰 처리

관련 endpoint:

```text
GET /public/rewards/grants
GET /public/rewards/grants/{grant_id}/options
POST /public/rewards/{grant_id}/choose
GET /public/coupons
POST /public/coupons/{coupon_id}/redeem
```

확인 포인트:

- `RewardGrant` 선택은 `FOR UPDATE`로 잠금 처리됩니다.
- 중복 선택 또는 unique 충돌은 409로 반환되어야 합니다.
- 쿠폰 만료 비교에도 `normalize()`가 적용됩니다.
- JSON payload 변경은 새 dict 할당 방식으로 저장되어야 합니다.

## 8. 운영 DB에서 직접 확인할 SQL 예시

특정 pass 기준:

```sql
SELECT id, status, issued_at, activated_at, expires_at, version
FROM wifi_passes
WHERE id = '<pass_id>';
```

claim 교환 여부:

```sql
SELECT oc.id, oc.order_id, oc.expires_at, oc.exchanged_at
FROM order_claims oc
JOIN orders o ON o.id = oc.order_id
WHERE o.id = '<order_id>';
```

OTP 상태:

```sql
SELECT id, order_id, status, attempts, expires_at, verified_at
FROM otp_challenges
WHERE order_id = '<order_id>'
ORDER BY created_at DESC;
```

이벤트 순서:

```sql
SELECT event_type, aggregate_type, aggregate_id, created_at
FROM backend_events
WHERE aggregate_id IN ('<order_id>', '<claim_id>', '<pass_id>')
ORDER BY created_at ASC;
```

관리자 활성 이용권 후보:

```sql
SELECT id, status, expires_at, activated_at
FROM wifi_passes
WHERE store_id = '<store_id>'
  AND status IN ('ACTIVE', 'EXPIRING_SOON')
ORDER BY issued_at DESC;
```

## 9. 이번 로컬 검증에서 확인된 실제 데이터

검증 환경:

- Backend: `http://127.0.0.1:8000`
- Admin frontend: `http://127.0.0.1:5173`
- Customer frontend: `http://127.0.0.1:5174`

검증 결과:

- Order ID: `31fb578a-0321-4705-a5d6-bf46d2b36e5a`
- Pass ID: `44d8f655-b928-4c91-a0b5-20e11f18bebf`
- `orders.status`: `PAID`
- `order_claims.exchanged_at`: present
- `otp_challenges.status`: `VERIFIED`
- `wifi_passes.status`: `ACTIVE`
- 관리자 `/admin/passes/active`: 해당 pass 포함

이벤트:

```text
order.created
order.claim.exchanged
wifi.pass.activated
```

## 10. 팀장 최종 승인 체크리스트

- [ ] 운영 API에서 QR 첫 진입 시 `/public/order-claims/exchange`가 200으로 응답하는지
- [ ] 운영 로그에서 aware/naive `TypeError`가 사라졌는지
- [ ] 고객 포털에서 `Failed to fetch` 대신 정상 인증 화면으로 이동하는지
- [ ] OTP 인증 후 이용권이 `ACTIVE`가 되는지
- [ ] 관리자 대시보드의 활성 이용권 수가 증가하는지
- [ ] `/admin/passes/active` 응답 배열에 동일 passId가 포함되는지
- [ ] provider 장애 시 pass가 잘못 `ACTIVE`로 남지 않는지
- [ ] POS 중복 주문/idempotency 재시도 시 500이 아닌 201 replay 또는 409가 반환되는지

