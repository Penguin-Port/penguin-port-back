# Smart WiFi Pass Backend MVP

카페 주문을 Wi-Fi 이용 시간과 당일 누적 리워드에 연결하는 **대회용 축소 MVP 백엔드**입니다.
첨부된 PDF의 시연 한 줄을 기준으로 구현합니다.

```text
주문 → QR/OTP → Wi-Fi 활성화·타이머 → 추가 주문 연장
→ 당일 누적 리워드 → Admin 연장/종료 → AI 추천 카드 승인
```

## 기술 스택

| 영역 | 기술 |
| --- | --- |
| 공개 MVP API | FastAPI |
| 전체 운영 API/Control Plane | Django + Django REST Framework |
| ORM | SQLAlchemy 2.x |
| 마이그레이션 | Alembic |
| 원본 DB | Supabase PostgreSQL (`DATABASE_URL`) |
| 관리자 인증 | FastAPI JWT |
| 고객 세션 | 짧은 JWT Portal Session |
| 백그라운드 작업 | Celery + Redis (`USE_CELERY=1`) |
| 실시간 이벤트 | 저장형 BackendEvent + SSE |
| 외부 연동 | Demo/Solapi/HTTP 알림, Demo/HTTP Wi-Fi, Demo/HTTP 트렌드 |
| 테스트 | pytest, FastAPI TestClient |

FastAPI는 대회용 축소 MVP 기본 실행 경로이고, Django는 전체 백엔드 기획서의 운영 API와
팀 권한·Outbox·Celery 작업 경로입니다. 두 경로는 같은 저장소에서 선택적으로 실행할 수 있으며,
`docker compose --profile full up`으로 Django와 Django worker/beat까지 함께 기동할 수 있습니다.

## 프로젝트 구조

```text
app/
├─ main.py                 # FastAPI 앱 + lifespan expire loop
├─ config.py               # DATABASE_URL, JWT_SECRET, DEMO_KEY
├─ db.py                   # SQLAlchemy engine/session
├─ models.py               # 최소 MVP 테이블
├─ schemas.py              # Pydantic 요청 스키마
├─ routers/
│  ├─ pos.py               # 주문·이용권·Claim·누적
│  ├─ public.py            # QR·OTP·Pass·Reward
│  └─ admin.py             # 로그인·Pass·AI
├─ services/
│  ├─ policy.py            # 첫/추가 주문 시간 정책
│  ├─ rewards.py           # 당일 누적·리워드 선택
│  ├─ wifi.py              # 만료 스캔·상태
│  ├─ recommendations.py   # OpenAI Structured Outputs + 규칙 fallback
│  ├─ events.py            # BackendEvent·Redis fan-out·SSE
│  ├─ notifications.py     # OTP provider 연결
│  └─ demo_network.py      # Demo/HTTP AP adapter
├─ celery_app.py           # FastAPI Celery 앱
├─ tasks.py                # FastAPI minutely/hourly/daily jobs
└─ seed.py                 # 데모 매장·메뉴·AI 카드 1건
integrations/
└─ providers.py            # SMS/알림톡·Wi-Fi·트렌드 공통 provider
alembic/
└─ versions/0001_initial.py
```

## 시작하기

### 1. 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env
```

### 2. 환경 변수

`.env`에서 Supabase 연결 문자열과 데모 키를 설정합니다.

```dotenv
DATABASE_URL=postgresql+psycopg://postgres:password@db.example.supabase.co:5432/postgres
JWT_SECRET=32자 이상의 운영용 비밀키
DEMO_KEY=demo-key
DEMO_OTP_CODE=123456
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-mini
OPENAI_TIMEOUT_SECONDS=20
EXPIRE_INTERVAL_SECONDS=60
REDIS_URL=
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1
USE_CELERY=0
NOTIFICATION_PROVIDER=DEMO
WIFI_NETWORK_PROVIDER=DEMO
TREND_PROVIDER=DEMO
```

로컬에서 빠르게 확인할 때 `DATABASE_URL=sqlite:///./smartpass.db`도 사용할 수 있습니다.

### 3. Alembic 마이그레이션과 시드

```bash
alembic upgrade head
python3 -m app.seed
```

기본 데모 계정은 `demo-owner / demo-password`입니다. 시드 명령은 다시 실행해도 같은 매장과
PENDING AI 카드가 중복 생성되지 않습니다.

### 4. 실행

```bash
uvicorn app.main:app --reload --env-file .env
```

로컬 SQLite 초기화, Alembic, 데모 시드, 서버 실행을 한 번에 하려면 다음 스크립트를 사용할 수
있습니다. 기본적으로 `/private/tmp/smartpass-manual.sqlite3`를 매번 새로 만들며, 기존 데이터를
유지하려면 `RESET_DB=0`을 지정합니다.

```bash
./scripts/run_local.sh
# 기존 DB 유지
RESET_DB=0 ./scripts/run_local.sh
```

- API: `http://127.0.0.1:8000`
- Swagger: `http://127.0.0.1:8000/docs`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

기본 Docker 실행은 `alembic upgrade head` 후 Uvicorn을 실행합니다. Redis/Celery와 PostgreSQL을
포함한 운영형 실행은 `docker compose up`을 사용하고, Django 전체 백엔드까지 포함하려면
`docker compose --profile full up`을 사용합니다. 상세 환경 변수는 `.env.example`에 있습니다.

## PDF MVP API

모든 경로는 PDF에 적힌 그대로 제공하며, 기존 프론트 전환을 위해 `/api/v1` 접두사도 별칭으로
지원합니다.

| Method | Endpoint | 용도 |
| --- | --- | --- |
| POST | `/pos/orders` | 주문·이용권·Claim·당일 누적 생성 |
| POST | `/pos/orders/{id}/refund` | 부분/전액 환불 및 누적·미사용 혜택 회수 |
| POST | `/public/order-claims/exchange` | QR Claim 교환 |
| POST | `/public/otp/send` | Demo Inbox OTP 발송 |
| POST | `/public/otp/confirm` | Portal Session 발급 |
| POST | `/public/passes/{id}/activate` | Wi-Fi 활성화 |
| GET | `/public/passes/{id}` | 상태·타이머·누적 조회 |
| GET | `/public/upsell-hint` | 다음 티어 잔액 조회 |
| GET | `/public/kiosk/upsell-hint` | 프론트 명세 호환 업셀 경로 |
| GET | `/public/rewards/grants/{grantId}/options` | 리워드 혜택 선택지 조회 |
| POST | `/public/rewards/{grantId}/choose` | 즉시 혜택/7일 쿠폰 선택 |
| GET | `/public/coupons` | 고객 쿠폰함 |
| POST | `/public/coupons/{id}/redeem` | 쿠폰 사용 |
| GET | `/public/stores/{id}/privacy-notice` | 전화번호 보관·폐기 안내 |
| POST | `/admin/login` | 점주 JWT 로그인 |
| POST | `/admin/refresh` | Refresh Token rotation |
| POST | `/admin/logout` | Refresh Token 폐기 |
| GET | `/admin/me` | 관리자 프로필·역할 조회 |
| GET | `/admin/passes/active` | 활성 이용권 폴링 목록 |
| POST | `/admin/passes/{id}/extend` | 수동 연장 |
| POST | `/admin/passes/{id}/expire` | 즉시 종료 + Demo revoke |
| GET | `/admin/events` | 관리자 실시간 SSE 이벤트 스트림 |
| GET | `/admin/team` | 관리자 팀 목록 |
| POST | `/admin/team` | 관리자 계정 생성 |
| PATCH | `/admin/team/{adminId}` | 관리자 역할·활성 상태·비밀번호 변경 |
| GET | `/admin/wifi/policies` | Wi-Fi 정책 조회 |
| POST | `/admin/wifi/policies/simulate` | 정책 미리 계산 |
| POST | `/admin/wifi/policies/publish` | 정책 버전 게시 |
| GET | `/admin/ai/recommendations` | AI 추천 카드 조회 |
| POST | `/admin/ai/recommendations/generate` | AI 추천 생성(OpenAI 또는 fallback) |
| GET | `/admin/ai/sales-summary` | 시간대 매출·주문·Wi-Fi 요약 |
| GET | `/admin/inventory` | 재고·위험도 조회 |
| POST | `/admin/inventory` | 재고 기준값 저장 |
| POST | `/admin/inventory/{id}/adjust` | 재고 조정 |
| POST | `/admin/inventory/scan` | 위험 스캔·프로모션 추천 생성 |
| GET | `/admin/ai/inventory` | 재고 프로모션 추천 조회 |
| GET | `/admin/ai/menu-trends` | 신메뉴 폴백 추천 카드 |
| GET | `/admin/rewards/tiers` | 리워드 티어·혜택 조회 |
| POST | `/admin/rewards/tiers` | 리워드 티어·혜택 게시 |
| GET | `/admin/audit` | 매장 감사 로그 조회 |
| PATCH | `/admin/ai/recommendations/{id}` | 추천 시간·메뉴·할인율 수정 |
| POST | `/admin/ai/recommendations/{id}/accept` | 추천 승인·프로모션 생성 |
| POST | `/admin/ai/recommendations/{id}/reject` | 추천 거절 |

### 추천 생성·시연 결정

이번 백엔드 시연의 AI 주제는 `TIME_SALE`로 확정합니다. 현재 FastAPI MVP에서
`TIME_POLICY`는 AI 추천 타입으로 구현되어 있지 않고, `/admin/wifi/policies/*`의 결정론적
Wi-Fi 시간 정책으로 동작합니다. 반면 `TIME_SALE`은 기획서·프론트 카드·현재 시드 데이터와
일치하고, 생성 → 관리자 수정 → 승인 → `Promotion` 생성 흐름을 한 번에 보여줄 수 있습니다.

시연 순서는 `TIME_SALE` 추천 생성 → 카드의 `source=OPENAI`와 근거 확인 → 필요 시 수정 →
관리자 승인으로 진행합니다. `OPENAI_API_KEY`가 없거나 호출/검증에 실패하면 같은 API가
`source=RULE_FALLBACK` 카드로 안전하게 전환됩니다. 생성만으로는 프로모션이 게시되지 않습니다.

```bash
export OPENAI_API_KEY="발급받은_프로젝트_API_키"
export OPENAI_MODEL="gpt-5-mini"

curl -X POST http://127.0.0.1:8000/admin/ai/recommendations/generate \
  -H 'Authorization: Bearer ADMIN_ACCESS_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"storeId":"STORE_UUID","type":"TIME_SALE"}'
```

응답의 `payload.source`가 `OPENAI`이면 실제 Responses API 생성 결과이며, `OPENAI` 추천도
Pydantic Structured Outputs와 매장 메뉴·할인율·시간 정책 검증을 통과한 경우에만 저장됩니다.

`NOTIFICATION_PROVIDER=SOLAPI`이면 Solapi SMS/알림톡 HMAC API를 호출하고, `HTTP`이면
`NOTIFICATION_BASE_URL` 게이트웨이를 호출합니다. `WIFI_NETWORK_PROVIDER=HTTP`는
`WIFI_AP_BASE_URL` 컨트롤러의 세션 생성/삭제 API를 사용합니다. 외부 트렌드는
`TREND_PROVIDER=HTTP`와 `TREND_API_BASE_URL`로 연결하며, 외부 서비스가 없으면 Demo provider로
안전하게 동작합니다.

Customer Portal 연동 응답에는 다음 표시용 필드가 포함됩니다.

- `POST /public/order-claims/exchange`: `storeName`, `orderNo`, `items`, `paidAmount`, `providedMinutes`
- `GET /public/passes/{id}`: `remainingSeconds` (응답 `meta.serverTime` 기준)
- `GET /public/rewards/grants/{grantId}/options`: `grantId`, `tierAmount`, `status`, `options`

### 인증

- POS: `X-Demo-Key: $DEMO_KEY`
- 고객: OTP 확인 응답의 `portalSession`을 `X-Portal-Session`에 전달
- 관리자: 로그인 응답의 `accessToken`을 `Authorization: Bearer ...`에 전달하고, `refreshToken`은 HttpOnly 쿠키에도 설정됩니다.

### 대표 호출 순서

```bash
# 1) POS 주문
curl -X POST http://127.0.0.1:8000/pos/orders \
  -H 'Content-Type: application/json' \
  -H 'X-Demo-Key: demo-key' \
  -d '{
    "storeId": "STORE_UUID",
    "externalOrderId": "ORDER-001",
    "customer": {"phone": "010-1234-5678"},
    "items": [{"productId": "PRODUCT_UUID", "quantity": 1, "unitPrice": 5000}],
    "totalAmount": 5000,
    "paidAt": "2026-08-01T12:00:00+09:00"
  }'

# 2) QR Claim 교환 → 응답 verificationTicket 사용
curl -X POST http://127.0.0.1:8000/public/order-claims/exchange \
  -H 'Content-Type: application/json' \
  -d '{"orderClaim": "ORDER_CLAIM_TOKEN"}'

# 3) OTP 발송·확인
curl -X POST http://127.0.0.1:8000/public/otp/send \
  -H 'Content-Type: application/json' \
  -d '{"verificationTicket": "VERIFICATION_TICKET", "phone": "010-1234-5678"}'

curl -X POST http://127.0.0.1:8000/public/otp/confirm \
  -H 'Content-Type: application/json' \
  -d '{"challengeId": "CHALLENGE_UUID", "code": "123456"}'
```

## 최소 비즈니스 규칙

- 첫 주문 기본 정책: 120분, 10,000원 이상 +30분, 15,000원 이상 +60분(관리자 정책 게시로 변경 가능)
- 추가 주문 기본 정책: 5,000원 이상 +60분, 10,000원 이상 +120분(관리자 정책 게시로 변경 가능)
- 이용권은 `version`, `expires_at`, `status`, `policy_snapshot`을 저장하고 연장 시 version을 올립니다.
- `USE_CELERY=0`이면 lifespan 만료 루프가 동작하고, `USE_CELERY=1`이면 Celery minutely task가 만료·개인정보·프로모션 상태를 처리합니다.
- 누적 티어는 5,000원과 10,000원 두 개이며 혜택은 최대 3개입니다.
- AI 시드는 “오후 2~4시 아메리카노 15% 할인 추천” PENDING 카드 한 건입니다.
- OTP 원문은 `otp_challenges`에 저장하지 않고 `demo_messages` Demo Inbox에만 남깁니다.
- 부분/전액 환불은 실제 환불 금액만 당일 누적에서 차감하고, 아직 사용하지 않은 하위 티어 리워드·쿠폰은 회수합니다.
- 모든 오류는 `type`, `title`, `status`, `code`, `detail`, `retryable`, `requestId` Problem JSON으로 반환합니다.
- 명시적 추천 생성 API의 `TIME_SALE`·`SALES_SUMMARY`는 `OPENAI_API_KEY`가 있을 때 OpenAI Responses API를 호출하고, 키가 없거나 실패하면 규칙 기반 fallback을 저장합니다. 재고·신메뉴 카드는 현재 규칙 기반입니다.
- `GET /admin/events`는 저장된 이벤트를 먼저 재생하고, 프로세스 내부 fan-out 또는 Redis Pub/Sub를 통해 새 이벤트를 SSE로 전달합니다.
- OTP는 주문 단위 발송 횟수를 제한하고, 보존기간이 지난 OTP/데모 메시지/전화 lookup 정보와 감사 로그를 lifespan 정리 루프에서 폐기합니다.

## 테스트

```bash
python3 -m pytest -q
```

통합 테스트는 주문 → QR → OTP → ACTIVE → 리워드 선택과 관리자 연장/종료/AI 승인 흐름을
SQLite에서 검증합니다.

## 문서

- [`docs/openapi.yaml`](docs/openapi.yaml)
- [`docs/schema.dbml`](docs/schema.dbml)
- [`alembic/versions/0001_initial.py`](alembic/versions/0001_initial.py)
- 기준 문서: `Smart_WiFi_Pass_Backend_MVP_Spec.pdf`
