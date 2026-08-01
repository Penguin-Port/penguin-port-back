# Smart WiFi Pass Backend

[Backend] Penguin Port 서비스의 백엔드 프로젝트입니다.

카페 주문을 Wi-Fi 이용 시간, 당일 누적 리워드, 운영 데이터와 연결하는 백엔드입니다.
기획 과정에서 제공된 백엔드 MVP 명세의 핵심 시연 흐름을 기준으로 구현했으며,
외부 SMS, 네트워크 장비, LLM이 없어도 로컬에서 전체 흐름을 검증할 수 있습니다.

> MVP 핵심 흐름: 주문 → QR/OTP → Wi-Fi 활성화·타이머 → 추가 주문 연장 →
> 당일 누적 리워드 → 관리자 연장·즉시 차단 → AI 추천 승인

## 기술 스택

| 영역 | 사용 기술 |
| --- | --- |
| 언어 | Python 3.12+ |
| 웹 프레임워크 | Django 5.x~6.x |
| API | Django REST Framework 3.15+ |
| 데이터베이스 | SQLite(로컬 기본), PostgreSQL |
| 인증 | PyJWT Access/Refresh Token, Django Session, Portal Session |
| 비밀번호 | Argon2id |
| 비동기 작업·캐시 | Celery, Redis(선택) |
| 테스트 | Django TestCase, DRF APIClient |
| API·ERD 문서 | OpenAPI 3.1, DBML |
| 패키지 관리 | pip, `requirements.txt` |

## 명세와 현재 구현

첨부된 MVP 명세는 대회 기간 내 시연을 위해 `FastAPI + Supabase` 단일 구성을 제안합니다.
이 저장소는 같은 제품 흐름을 **Django + Django REST Framework**로 구현한 실행 가능한
백엔드입니다. 따라서 제품 목표와 완료 기준은 MVP 명세를 따르지만, 실행 방법과 API 계약은
이 저장소의 코드 및 [`docs/openapi.yaml`](docs/openapi.yaml)을 기준으로 합니다.

| 구분 | MVP 명세의 제안 | 현재 저장소 구현 |
|---|---|---|
| 애플리케이션 서버 | FastAPI 단일 서버 | Django + Django REST Framework |
| 데이터베이스 | Supabase PostgreSQL | SQLite 기본, PostgreSQL 선택 |
| 관리자 인증 | Supabase Auth 또는 JWT | JWT Access/Refresh + Django Session |
| 만료 처리 | 프로세스 내부 주기 루프 | 버전 기반 예약 작업 + Worker 함수 |
| SMS·네트워크 | Demo Inbox / Demo Network | Demo Provider / DemoNetworkAdapter |
| AI | 고정 추천 또는 선택적 LLM | 규칙 기반 추천, Provider 교체 가능 |

## 주요 기능

- 결제 주문 생성과 Idempotency 응답 재사용
- 첫 주문 Wi-Fi 발급과 추가 주문 자동 연장
- 일회용 QR Claim, OTP 인증, Portal Session 발급
- Wi-Fi 활성화, 잔여 시간 조회, 만료·차단·해제
- 영업일 기준 당일 누적 금액과 리워드 티어 판정
- 즉시 혜택 또는 7일 쿠폰 선택·사용
- 관리자 주문 조회·환불과 누적 금액 롤백
- Wi-Fi 정책 버전 관리, 계산 미리보기, 게시
- 재고 등록·조정, 음수 재고 차단, 유통기한 위험 탐지
- 시간대 매출 집계와 타임세일·재고·신메뉴 추천
- 저장된 AI 추천 승인 후 프로모션 생성
- PDF 축소 MVP 경로(`/public/otp/*`, `/admin/passes/*`) 호환 제공
- `seed_mvp` 명령으로 데모 매장·메뉴·5천/1만원 리워드·AI 카드 1건 생성
- 매장별 OWNER/MANAGER/STAFF/VIEWER 권한 관리
- 공개 UUID 기반 관리자 사용자 식별
- 감사 로그, 알림 이력, 개인정보 보존 정책, Outbox Event, SSE

## 시스템 흐름

```mermaid
flowchart LR
    POS["POS 주문"] --> Claim["QR Claim"]
    Claim --> OTP["OTP 인증"]
    OTP --> Pass["Wi-Fi 활성화"]
    Pass --> Extend["추가 주문 연장"]
    Extend --> Reward["당일 누적 리워드"]
    Reward --> Admin["관리자 제어"]
    Admin --> AI["AI 추천 승인"]
    AI --> Promotion["프로모션 생성"]
```

## 프로젝트 구조

```text
penguin-port-back/
├─ api/          # Serializer, API View, URL, 통합 테스트
│  └─ management/commands/seed_mvp.py # PDF MVP 데모 시드
├─ stores/       # 매장과 팀 권한
├─ catalog/      # 메뉴와 카테고리
├─ orders/       # 주문, Claim, Idempotency
├─ identity/     # OTP, JWT 세션, 공개 사용자 UUID
├─ wifi/         # 정책, 이용권, 연장, 만료 작업
├─ rewards/      # 당일 누적, 티어, 혜택, 쿠폰
├─ inventory/    # 재고, 수량 조정, 위험 탐지
├─ ai_ops/       # 분석, AI 추천, 프로모션
├─ operations/   # 감사, 알림, 개인정보, Outbox
├─ config/       # Django 설정
├─ docs/         # OpenAPI와 ERD
└─ scripts/      # API 문서 생성 스크립트
```

## 시작하기

### 요구 사항

- Python 3.12 이상
- pip
- Git
- PostgreSQL과 Redis는 선택 사항이며, 없어도 SQLite 기반 로컬 실행과 테스트가 가능합니다.

### 1. 저장소 준비

```bash
git clone https://github.com/GoRhanHee/penguin-port-back.git
cd penguin-port-back

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

### 2. 마이그레이션

환경 변수가 없으면 로컬 `db.sqlite3`를 사용합니다.

```bash
python3 manage.py migrate
```

### 3. PDF MVP 데모 데이터(선택)

매장·아메리카노·Wi-Fi 정책·5천/1만원 리워드·PENDING AI 추천 카드와 점주 계정을 한 번에
생성합니다. 같은 명령을 다시 실행해도 중복 생성하지 않습니다.

```bash
python3 manage.py seed_mvp
# 기본 계정: demo-owner / demo-password
```

매장명과 계정은 옵션으로 바꿀 수 있습니다.

```bash
python3 manage.py seed_mvp --store-name "테스트 카페" \
  --username owner --password 'change-me'
```

### 4. 관리자 계정 생성

```bash
python3 manage.py createsuperuser
```

생성 후 Django Admin(`http://127.0.0.1:8000/admin/`)에서 매장, 상품, 정책,
리워드 티어 등 시연 데이터를 등록할 수 있습니다.

### 5. 서버 실행

```bash
python3 manage.py runserver
```

- API Base URL: `http://127.0.0.1:8000/api/v1`
- Django Admin: `http://127.0.0.1:8000/admin/`

## 주요 명령어

| 명령 | 설명 |
| --- | --- |
| `source .venv/bin/activate` | Python 가상환경 활성화 |
| `python3 manage.py migrate` | 데이터베이스 마이그레이션 적용 |
| `python3 manage.py createsuperuser` | Django 관리자 계정 생성 |
| `python3 manage.py runserver` | 로컬 개발 서버 실행 |
| `python3 manage.py seed_mvp` | PDF 축소 MVP 시연 데이터와 PENDING AI 카드 생성 |
| `python3 manage.py test` | 전체 자동 테스트 실행 |
| `python3 manage.py test --verbosity 2` | 테스트 이름과 상세 결과 출력 |
| `python3 manage.py check` | Django 설정·모델 시스템 검사 |
| `python3 manage.py makemigrations --check --dry-run` | 누락된 마이그레이션 검사 |
| `python3 scripts/generate_openapi.py` | OpenAPI 문서 재생성 |

## 환경 변수

| 변수 | 필수 여부 | 설명 | 기본 동작 |
|---|---:|---|---|
| `DJANGO_SECRET_KEY` | 운영 필수 | JWT·서명에 사용하는 Django 비밀키 | 개발용 기본값 |
| `DJANGO_DEBUG` | 아니요 | Django Debug 활성화 여부 | `true` |
| `DJANGO_ALLOWED_HOSTS` | 운영 권장 | 쉼표로 구분한 허용 Host | `*` |
| `POSTGRES_DB` | 아니요 | PostgreSQL 데이터베이스 이름 | 미설정 시 SQLite |
| `POSTGRES_USER` | PostgreSQL 사용 시 | PostgreSQL 사용자 | 없음 |
| `POSTGRES_PASSWORD` | PostgreSQL 사용 시 | PostgreSQL 비밀번호 | 없음 |
| `POSTGRES_HOST` | 아니요 | PostgreSQL Host | `localhost` |
| `POSTGRES_PORT` | 아니요 | PostgreSQL Port | `5432` |
| `POS_HMAC_SECRET` | 운영 권장 | POS 요청 서명 검증 키 | 미설정 시 로컬 데모 모드 |
| `DEMO_OTP_CODE` | 아니요 | 로컬 OTP 고정 코드 | `123456` |
| `REDIS_URL` | 아니요 | Redis Cache 주소 | Django 기본 Cache |
| `CELERY_BROKER_URL` | 아니요 | Celery Broker 주소 | `redis://localhost:6379/0` |
| `CELERY_RESULT_BACKEND` | 아니요 | Celery 결과 저장소 | `redis://localhost:6379/1` |

운영 환경에서는 개발용 `DJANGO_SECRET_KEY`와 `DEMO_OTP_CODE`를 그대로 사용하면 안 됩니다.

## API 공통 규칙

### Base URL

모든 REST API 경로 앞에는 `/api/v1`이 붙습니다.

```text
http://127.0.0.1:8000/api/v1
```

### 인증 방식

| API 영역 | 인증 방식 |
|---|---|
| POS | `X-Timestamp`, `X-Nonce`, `X-Signature`, `Idempotency-Key` |
| QR·OTP 시작/확인 | 인증 없음 |
| 고객 이용권·리워드·쿠폰 | `X-Portal-Session` |
| 관리자 | `Authorization: Bearer <accessToken>` 또는 Django Session |
| 관리자 Refresh | HttpOnly Cookie `smartpass_refresh` |

`POS_HMAC_SECRET`이 설정된 환경에서는 POS 서명이 필수입니다. 서명 대상은
`timestamp + nonce + raw request body`이며 HMAC-SHA256을 사용합니다.

### 성공 응답

```json
{
  "data": {},
  "meta": {
    "requestId": "request-id",
    "serverTime": "2026-08-01T00:00:00Z"
  }
}
```

### 오류 응답

```json
{
  "type": "https://api.smart-wifi-pass.local/problems/inventory-rule-violation",
  "title": "Inventory Rule Violation",
  "status": 422,
  "code": "INVENTORY_RULE_VIOLATION",
  "detail": "재고 수량은 0보다 작을 수 없습니다.",
  "retryable": false,
  "requestId": "request-id"
}
```

주요 상태 코드는 `400` 입력 오류, `401` 인증 실패, `403` 권한 부족,
`409` 상태·버전 충돌, `410` 만료, `422` 비즈니스 규칙 위반,
`429` 요청 제한입니다.

## API 목록

현재 OpenAPI 명세에는 **55개 경로, 62개 Operation**이 정의되어 있습니다.
기존 확장 API를 유지하면서 PDF 축소 MVP에 적힌 짧은 경로도 함께 제공합니다.

### POS

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/pos/orders` | 결제 주문 생성, 누적·리워드 평가, Wi-Fi 발급·연장 |
| `POST` | `/pos/rewards/immediate/{redemptionId}/apply` | 즉시 리워드를 주문에 적용 |

### 고객 인증과 개인정보 안내

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/public/order-claims/exchange` | QR 주문 Claim 교환 |
| `POST` | `/public/verifications/start` | OTP 인증 시작 |
| `POST` | `/public/verifications/confirm` | OTP 확인 및 Portal Session 발급 |
| `POST` | `/public/otp/send` | PDF MVP OTP 발송 경로 |
| `POST` | `/public/otp/confirm` | PDF MVP OTP 확인 경로 |
| `GET` | `/public/stores/{storeId}/privacy-notice` | 개인정보 처리 안내 조회 |

### 고객 Wi-Fi

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/public/passes/{passId}` | Wi-Fi 상태, 만료 시각, 누적 현황 조회 |
| `POST` | `/public/passes/{passId}/activate` | Wi-Fi 이용권 활성화 |
| `GET` | `/public/kiosk/upsell-hint` | 다음 리워드 티어까지 필요한 금액 조회 |
| `GET` | `/public/upsell-hint` | PDF MVP 업셀 경로 |

### 고객 리워드와 쿠폰

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/public/rewards/grants/{grantId}/options` | 선택 가능한 리워드 혜택 조회 |
| `POST` | `/public/rewards/grants/{grantId}/choose` | 즉시 혜택 또는 7일 쿠폰 선택 |
| `POST` | `/public/rewards/{grantId}/choose` | PDF MVP 리워드 선택 경로 |
| `GET` | `/public/coupons` | 고객 쿠폰함 조회 |
| `POST` | `/public/coupons/{couponId}/redeem` | 쿠폰 사용 |

### 관리자 인증

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/admin/auth/login` | 로그인 및 Access Token 발급 |
| `POST` | `/admin/login` | PDF MVP 점주 로그인 경로 |
| `POST` | `/admin/auth/refresh` | Refresh Token 회전 |
| `POST` | `/admin/auth/logout` | 로그아웃 및 Refresh Token 폐기 |
| `GET` | `/admin/auth/me` | 현재 관리자와 매장 권한 조회 |

### 관리자 주문과 메뉴

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/admin/orders` | 주문 목록 조회 |
| `POST` | `/admin/orders/{orderId}/refund` | 부분·전체 환불 및 누적 금액 롤백 |
| `GET` | `/admin/catalog/products` | 메뉴 목록 조회 |
| `POST` | `/admin/catalog/products` | 메뉴 생성 |

### 관리자 Wi-Fi 정책과 이용권

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/admin/wifi/policies` | Wi-Fi 정책 버전 목록 |
| `POST` | `/admin/wifi/policies` | 새 정책 버전 생성 |
| `POST` | `/admin/wifi/policies/simulate` | 주문 금액별 제공 시간 계산 |
| `POST` | `/admin/wifi/policies/{policyId}/publish` | 정책 게시 |
| `GET` | `/admin/wifi/live-passes` | 활성·임박 이용권 목록 |
| `GET` | `/admin/wifi/passes/{passId}/extensions` | 이용권 연장 이력 |
| `POST` | `/admin/wifi/passes/{passId}/actions` | 수동 연장·차단·해제 |
| `GET` | `/admin/passes/active` | PDF MVP 실시간 이용권 목록(폴링) |
| `POST` | `/admin/passes/{passId}/extend` | PDF MVP 수동 연장 |
| `POST` | `/admin/passes/{passId}/expire` | PDF MVP 즉시 종료 및 Demo revoke |

### 관리자 리워드

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/admin/rewards/tiers` | 리워드 티어 목록 |
| `POST` | `/admin/rewards/tiers` | 티어와 혜택 풀 생성 |
| `GET` | `/admin/rewards/history` | 리워드 지급 이력 |

### 관리자 재고

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/admin/inventory` | 재고 목록 조회 |
| `POST` | `/admin/inventory` | 재고 품목 생성, `quantity >= 0` 검증 |
| `POST` | `/admin/inventory/{itemId}/adjust` | 재고 증감, 결과가 음수면 거부 |
| `POST` | `/admin/inventory/scan` | 품절·유통기한 위험 탐지와 추천 생성 |

### 관리자 AI와 프로모션

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/admin/ai/recommendations` | AI 추천 목록 |
| `POST` | `/admin/ai/recommendations/generate` | 매출·타임세일·재고·신메뉴 추천 생성 |
| `PATCH` | `/admin/ai/recommendations/{recommendationId}` | 추천 내용 수정 |
| `POST` | `/admin/ai/recommendations/{recommendationId}/accept` | 저장된 추천을 승인하고 프로모션 생성 |
| `POST` | `/admin/ai/recommendations/{recommendationId}/reject` | 추천 거절 |
| `GET` | `/admin/promotions` | 프로모션 목록 |
| `PATCH` | `/admin/promotions/{promotionId}` | 프로모션 내용·기간·상태 수정 |

AI 추천 승인 요청에는 중복된 프로모션 정보를 다시 보내지 않습니다.

```json
{
  "storeId": "00000000-0000-0000-0000-000000000000",
  "version": 1
}
```

`title`, `payload`, `startsAt`, `endsAt`은 추천 생성·수정 단계에서 저장된 값을 사용합니다.

### 관리자 운영·팀·개인정보

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/admin/analytics/hourly` | 시간대별 주문·매출·Wi-Fi 지표 |
| `GET` | `/admin/audit` | 감사 로그 목록 |
| `GET` | `/admin/notifications` | 알림 발송 이력 |
| `GET` | `/admin/anomalies` | 실패 이용권·알림·지연 작업 현황 |
| `GET` | `/admin/team` | 매장 팀원과 역할 조회 |
| `POST` | `/admin/team` | UUID `userId`로 팀원 역할 등록·수정 |
| `GET` | `/admin/privacy/retention` | 개인정보 보존 정책 조회 |
| `PATCH` | `/admin/privacy/retention` | 개인정보 보존 정책 수정 |
| `GET` | `/admin/privacy/export` | 점주 보호용 주문·인증 증빙 내보내기 |
| `GET` | `/admin/settings` | 매장 설정 조회 |
| `PATCH` | `/admin/settings` | 매장 설정 수정 |
| `GET` | `/stores/{storeId}/events` | 매장별 Server-Sent Events 스트림 |

## 대표 API 호출 예시

### 1. 관리자 로그인

```bash
curl -X POST http://127.0.0.1:8000/api/v1/admin/auth/login \
  -H 'Content-Type: application/json' \
  -c cookies.txt \
  -d '{
    "username": "owner",
    "password": "your-password"
  }'
```

응답의 `data.accessToken`을 이후 관리자 요청의 Bearer Token으로 사용합니다.

```bash
curl 'http://127.0.0.1:8000/api/v1/admin/auth/me' \
  -H 'Authorization: Bearer YOUR_ACCESS_TOKEN'
```

### 2. 주문 생성

로컬 데모에서 `POS_HMAC_SECRET`을 설정하지 않았다면 서명 없이 흐름을 확인할 수 있습니다.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/pos/orders \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-order-001' \
  -d '{
    "storeId": "STORE_UUID",
    "externalOrderId": "ORDER-001",
    "customer": {"phone": "010-1234-5678"},
    "items": [
      {
        "productId": "PRODUCT_UUID",
        "quantity": 1,
        "unitPrice": 5000
      }
    ],
    "totalAmount": 5000,
    "paidAt": "2026-08-01T12:00:00+09:00"
  }'
```

응답의 `orderClaim.token`을 QR 값으로 사용합니다.

### 3. QR Claim과 OTP

```bash
curl -X POST http://127.0.0.1:8000/api/v1/public/order-claims/exchange \
  -H 'Content-Type: application/json' \
  -d '{"orderClaim": "ORDER_CLAIM_TOKEN"}'
```

```bash
curl -X POST http://127.0.0.1:8000/api/v1/public/verifications/start \
  -H 'Content-Type: application/json' \
  -d '{
    "verificationTicket": "VERIFICATION_TICKET",
    "phone": "010-1234-5678"
  }'
```

로컬 기본 OTP는 `123456`입니다.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/public/verifications/confirm \
  -H 'Content-Type: application/json' \
  -d '{
    "challengeId": "CHALLENGE_UUID",
    "code": "123456"
  }'
```

응답의 `portalSession`을 고객 API의 `X-Portal-Session` 헤더로 사용합니다.

## 테스트

전체 테스트는 독립된 인메모리 테스트 데이터베이스에서 실행됩니다.

```bash
python3 manage.py test
```

현재 테스트 스위트는 30개 테스트로 다음 핵심 흐름을 검증합니다.

- 주문 생성, 추가 주문 연장, Idempotency
- QR Claim, OTP, Portal Session, Wi-Fi 활성화
- 당일 누적 리워드, 즉시 혜택, 쿠폰 사용
- 관리자 로그인과 Refresh Token 회전
- 환불과 누적 금액 롤백
- 음수 재고 생성 차단과 재고 위험 추천
- 공개 UUID 기반 팀원 등록
- 저장된 AI 추천 승인과 프로모션 생성
- Wi-Fi 정책 계산과 오래된 만료 작업 방지
- 모든 Django API 경로와 OpenAPI 경로의 일치
- PDF MVP 별칭 경로, 직접 만료 스캔, `seed_mvp` 시드 결과

테스트 이름까지 확인하려면 다음 명령을 사용합니다.

```bash
python3 manage.py test --verbosity 2
```

마이그레이션과 모델이 일치하는지 별도로 확인할 수 있습니다.

```bash
python3 manage.py makemigrations --check --dry-run
python3 manage.py check
```

## API 문서와 ERD

- OpenAPI 3.1: [`docs/openapi.yaml`](docs/openapi.yaml)
- DBML ERD: [`docs/schema.dbml`](docs/schema.dbml)
- ERD 설명: [`docs/ERD_SPEC.md`](docs/ERD_SPEC.md)
- 구현 상태: [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md)

OpenAPI 문서는 아래 명령으로 다시 생성합니다.

```bash
python3 scripts/generate_openapi.py
```

## 데모와 운영 환경의 차이

현재 저장소는 외부 사업자 자격증명 없이도 심사·개발 환경에서 흐름을 재현하도록 구성되어
있습니다. 운영 전에는 다음 구현을 실제 Provider와 인프라로 교체해야 합니다.

| 현재 | 운영 전 교체 대상 |
|---|---|
| 고정 OTP / Demo 알림 | SMS 또는 알림톡 Provider |
| DemoNetworkAdapter | RADIUS, UniFi, MikroTik 등 실제 AP Adapter |
| 규칙 기반 AI 추천 | JSON Schema 검증을 포함한 LLM Gateway |
| Django 기본 Cache | Redis Cache, Rate Limit, SSE Pub/Sub |
| 직접 호출 가능한 Worker 함수 | Celery Worker/Beat 또는 배포 환경의 스케줄러 |
| SQLite | PostgreSQL 또는 관리형 PostgreSQL |

Wi-Fi만으로 장기 체류 문제를 해결한다고 가정하지 않습니다. 이 MVP는 추가 주문 유도,
당일 누적 리워드, 관리자 제어, AI 추천 승인까지 연결해 회전과 매출을 함께 개선할 수 있는
운영 흐름을 검증하는 데 목적이 있습니다.
