# PDF 축소 MVP 구현 현황

| 요구사항 | 구현 |
| --- | --- |
| FastAPI 단일 서버 | `app/main.py` |
| SQLAlchemy ORM | `app/models.py` |
| Supabase PostgreSQL 연결 | `DATABASE_URL` + psycopg |
| Alembic 마이그레이션 | `alembic upgrade head` |
| Demo 매장·메뉴·AI 카드 시드 | `python3 -m app.seed` |
| POS X-Demo-Key | `POST /pos/orders` |
| QR Claim | `POST /public/order-claims/exchange` |
| QR 교환 주문 표시 정보 | exchange 응답의 `storeName`, `orderNo`, `items`, `paidAmount`, `providedMinutes` |
| OTP Demo Inbox | `POST /public/otp/send`, `/public/otp/confirm` |
| Portal Session | JWT 발급·검증 |
| Wi-Fi 활성화·타이머 | `POST/GET /public/passes/{id}` + `remainingSeconds` |
| 추가 주문 연장 | 주문 금액 구간별 자동 연장 |
| 5천/1만원 누적 리워드 | `daily_spend_balances` + `reward_grants` |
| 리워드 선택지 조회 | `GET /public/rewards/grants/{grantId}/options` |
| 즉시/7일 쿠폰 선택 | `POST /public/rewards/{grantId}/choose` |
| Admin 로그인 | `POST /admin/login` |
| Admin 활성 목록·연장·종료 | `/admin/passes/*` |
| AI 추천 1건 승인·거절 | `/admin/ai/recommendations/*` |
| 60초 만료 루프 | FastAPI lifespan `expire_due_passes` |
| 별도 Worker/Redis/Celery | 사용하지 않음 |
| Docker Compose 배포 | 사용하지 않음 |
