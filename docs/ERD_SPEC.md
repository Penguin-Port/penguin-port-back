# Smart WiFi Pass MVP ERD

`schema.dbml`은 PDF 축소 MVP의 최소 테이블을 기준으로 작성했습니다.

## 도메인

- 매장·메뉴: `stores`, `products`
- 주문: `orders`, `order_items`, `order_claims`
- 인증 데모: `otp_challenges`, `demo_messages`, `admin_users`
- Wi-Fi: `wifi_passes` (`version`, `expires_at`, `policy_snapshot` 포함)
- 누적 리워드: `daily_spend_balances`, `reward_tiers`, `reward_benefits`, `reward_grants`, `coupons`
- 즉시 혜택 소비: `reward_redemptions` (현재/다음 주문에 적용)
- AI: `ai_recommendations`, `promotions`
- 분석: `analytics_hourly`
- 재고: `inventory_items`, `inventory_events`
- 운영: `audit_logs`, `refresh_token_sessions`, `idempotency_records`

`scheduled_actions` 테이블은 PDF에서 생략 가능하므로 두지 않았습니다. FastAPI lifespan 만료 루프가
`wifi_passes.expires_at`을 직접 스캔합니다.
