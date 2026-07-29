# Smart WiFi Pass Backend

두 PDF 기획서의 백엔드 범위를 Django와 Django REST Framework로 구현한
실행 가능한 MVP입니다. 외부 사업자 자격증명이 필요한 기능은 Demo Provider와
규칙 기반 폴백으로 동작합니다.

## 구현된 기능

- 매장, 역할, 메뉴, 주문, 재고, 분석, 감사 모델
- POS HMAC 검증, nonce 차단, Idempotency 응답 재사용
- 결제 주문 생성, 영업일 계산, 부분·전체 환불
- 당일 누적 구매액과 리워드 티어 중복 지급 방지
- 환불 시 누적 금액 롤백, 미선택 리워드·미사용 쿠폰 회수
- 첫 주문, 추가 주문, Quiet Hours, Wi-Fi 종일권
- `pass_version` 기반 만료·알림·접속 해제 작업
- 일회용 QR Claim, OTP, Portal Session
- 리워드 선택, 7일 쿠폰 발급·조회·사용
- 재고 수량 조정, 유통기한 위험 점수, 프로모션 추천
- 시간대 매출 집계, 매출 요약, 타임세일·신메뉴 추천 폴백
- AI 추천 수정·승인·거절과 프로모션 생성
- JWT Access·Refresh Rotation과 Session 인증, 매장 RBAC, 감사 로그
- 개인정보 보존 정책과 자동 폐기 함수
- Outbox Event와 SSE
- 47개 경로, 54개 Operation의 OpenAPI 3.1 명세
- dbdiagram.io용 DBML ERD

## 실행

```bash
python3 -m pip install -r requirements.txt
python3 manage.py migrate
python3 manage.py runserver
```

환경 변수가 없으면 SQLite를 사용합니다. PostgreSQL 사용 시 아래 값을 지정합니다.

```text
POSTGRES_DB
POSTGRES_USER
POSTGRES_PASSWORD
POSTGRES_HOST
POSTGRES_PORT
POS_HMAC_SECRET
DEMO_OTP_CODE
```

`POS_HMAC_SECRET`을 설정하면 POS 요청의 `X-Timestamp`, `X-Nonce`,
`X-Signature` 검사가 활성화됩니다. `DEMO_OTP_CODE`의 기본값은
로컬 시연용 `123456`이며, 운영 환경에서는 빈 값으로 설정해 Provider가 생성한
OTP를 사용해야 합니다.

## 테스트

```bash
python3 manage.py test
python3 scripts/generate_openapi.py
```

## API 문서

정식 계약은 `docs/openapi.yaml`에 있습니다. Swagger Editor 또는 Redoc에서 열 수 있습니다.

API 영역:

| 영역 | 대표 경로 | 설명 |
|---|---|---|
| POS | `/api/v1/pos/orders` | 주문·누적·리워드·Wi-Fi 트랜잭션 |
| 고객 인증 | `/api/v1/public/verifications/*` | QR와 OTP |
| 고객 Wi-Fi | `/api/v1/public/passes/*` | 조회·활성화 |
| 고객 리워드 | `/api/v1/public/rewards/*`, `/coupons/*` | 선택·쿠폰 |
| Admin 주문·메뉴 | `/api/v1/admin/orders`, `/catalog/products` | 조회·환불·메뉴 |
| Admin 정책 | `/api/v1/admin/wifi/*`, `/rewards/*` | 정책·티어 |
| Admin AI | `/api/v1/admin/ai/*`, `/promotions` | 생성·승인·거절 |
| 운영 | `/api/v1/admin/inventory`, `/audit`, `/notifications` | 재고·감사·알림 |
| 실시간 | `/api/v1/stores/{storeId}/events` | SSE |

## 외부 운영 환경에서 교체할 Provider

- Demo SMS → 실제 SMS 또는 알림톡 사업자
- DemoNetworkAdapter → RADIUS, UniFi, MikroTik Adapter
- 규칙 기반 AI 폴백 → OpenAI JSON Schema Gateway
- Django 기본 캐시 → Redis 캐시·Rate Limit·SSE Pub/Sub
- 직접 호출 가능한 Worker 함수 → Celery Beat 운영 프로세스
