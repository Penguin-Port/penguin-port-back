# 백엔드 구현 현황

| 요구사항 | 구현 |
| --- | --- |
| FastAPI 단일 서버 | `app/main.py` |
| Django 전체 API 경로 | `config/urls.py`, `api/` |
| SQLAlchemy ORM | `app/models.py` |
| Supabase PostgreSQL 연결 | `DATABASE_URL` + psycopg |
| Alembic 마이그레이션 | `alembic upgrade head` |
| Demo 매장·메뉴·AI 카드 시드 | `python3 -m app.seed` |
| POS X-Demo-Key | `POST /pos/orders` |
| QR Claim | `POST /public/order-claims/exchange` |
| QR 교환 주문 표시 정보 | exchange 응답의 `storeName`, `orderNo`, `items`, `paidAmount`, `providedMinutes` |
| 주문 합계·영업일·전화 식별자 | 항목 합계 검증, Store Timezone/cutoff, phone lookup hash/last4 |
| POS 멱등성 | `Idempotency-Key`와 `idempotency_records` 재응답 |
| 부분/전액 환불 | `POST /pos/orders/{id}/refund`, 누적 차감·미사용 Grant/Coupon 회수 |
| OTP Demo Inbox | `POST /public/otp/send`, `/public/otp/confirm` |
| Portal Session | JWT 발급·검증 |
| Wi-Fi 활성화·타이머 | `POST/GET /public/passes/{id}` + `remainingSeconds` |
| 추가 주문 연장 | 주문 금액 구간별 자동 연장 |
| 5천/1만원 누적 리워드 | `daily_spend_balances` + `reward_grants` |
| 리워드 선택지 조회 | `GET /public/rewards/grants/{grantId}/options` |
| 즉시/7일 쿠폰 선택 | `POST /public/rewards/{grantId}/choose` |
| 쿠폰함·쿠폰 사용 | `GET /public/coupons`, `POST /public/coupons/{id}/redeem` |
| 개인정보 안내 | `GET /public/stores/{id}/privacy-notice` |
| 즉시 혜택 소비 | `reward_redemptions`, 다음 주문 자동 소비, Wi-Fi 종일권 |
| Admin 로그인 | `POST /admin/login` |
| Admin Refresh Rotation | `/admin/refresh`, `/admin/logout`, `/admin/me` |
| Admin 활성 목록·연장·종료 | `/admin/passes/*` |
| Wi-Fi 정책 | `/admin/wifi/policies` 조회·simulate·publish + 버전 충돌 |
| AI 추천 1건 승인·거절 | `/admin/ai/recommendations/*` |
| 실제 OpenAI 추천 생성 | `POST /admin/ai/recommendations/generate` + Responses API Structured Outputs + 규칙 fallback |
| Django OpenAI 추천 생성 | `ai_ops/providers.py`, `ai_ops/services.py` |
| AI 추천 수정·검증 | `PATCH` 후 할인율·메뉴·시간·Promotion 생성 검증 |
| 공통 오류 규격 | Problem JSON + requestId 예외 핸들러 |
| AI 매출 요약 | `/admin/ai/sales-summary` + `analytics_hourly` 저장 |
| 재고 위험·프로모션 | `inventory_items/events` + 규칙 기반 `INVENTORY_PROMOTION` |
| 신메뉴 추천 폴백 | `/admin/ai/menu-trends` |
| 리워드 티어·혜택 관리 | `/admin/rewards/tiers` 조회·게시 |
| 감사 로그·보존 정리 | `/admin/audit`, lifespan 민감정보/감사 로그 purge |
| OTP Rate Limit | 주문 단위 발송 윈도우 제한 |
| 60초 만료 루프 | FastAPI lifespan `expire_due_passes` |
| Redis 이벤트 fan-out | `app/services/events.py` |
| FastAPI Celery/Beat | `app/celery_app.py`, `app/tasks.py` |
| Django Celery/Beat | `config/celery.py`, `operations/celery_tasks.py` |
| 관리자 SSE | FastAPI `/admin/events`, Django `/api/v1/stores/{storeId}/events` |
| 관리자 팀/역할 관리 | FastAPI `/admin/team`, Django `/api/v1/admin/team` |
| 실제 SMS/알림톡 provider | `integrations/providers.py` (`SOLAPI`/`HTTP`) |
| 실제 Wi-Fi AP provider | `integrations/providers.py` (`HTTP`) |
| 외부 트렌드 provider | `integrations/providers.py` (`HTTP`) |
| Docker Compose 운영형 구성 | `docker-compose.yml` |
