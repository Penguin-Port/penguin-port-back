# Smart WiFi Pass ERD 명세

## 사용 방법

1. [dbdiagram.io](https://dbdiagram.io/d)에 접속한다.
2. 새 Diagram을 생성한다.
3. `schema.dbml` 전체 내용을 왼쪽 DBML 편집기에 붙여 넣는다.
4. 자동 생성된 관계도를 확인하고 PDF 또는 PNG로 내보낸다.

## 도메인별 색상

| 색상 | 도메인 | 주요 테이블 |
|---|---|---|
| 파랑 | 사용자·매장·데모 인증 | `auth_users`, `stores`, `store_memberships`, `verification_challenges`, `demo_messages` |
| 청록 | 메뉴 | `product_categories`, `products` |
| 주황 | 주문 | `orders`, `order_items`, `order_claims` |
| 보라 | 리워드 | `daily_spend_balances`, `reward_grants`, `coupons` |
| 하늘 | Wi-Fi | `wifi_policies`, `wifi_passes`, `scheduled_actions` |
| 분홍 | AI 운영 | `ai_recommendations`, `promotions` |
| 초록 | 재고 | `inventory_items`, `inventory_events` |
| 회색 | 운영 | `notifications`, `audit_logs`, `outbox_events` |

## 핵심 관계

```text
Store
 ├─ ProductCategory ─ Product ─ OrderItem ─ Order
 ├─ RewardTier ─ RewardTierBenefit
 │                └─ RewardGrant ─ Coupon
 ├─ WiFiPolicy ─ WiFiAmountTier
 ├─ WiFiPass ─ PassExtension ─ Order
 ├─ AIRecommendation ─ Promotion
 ├─ InventoryItem ─ InventoryEvent
 └─ OutboxEvent / AuditLog / Notification / DemoMessage
```

## 무결성 규칙

- `orders(store_id, external_order_id)`는 중복될 수 없다.
- `daily_spend_balances(store_id, business_date, customer_key)`는 한 행만 존재한다.
- 같은 고객에게 같은 영업일의 동일 리워드 티어를 두 번 지급할 수 없다.
- 하나의 `reward_grant`에서는 쿠폰을 최대 한 개만 발급한다.
- 매장별 Wi-Fi 정책 버전은 중복될 수 없다.
- 하나의 AI 추천으로 프로모션을 최대 한 개만 생성한다.

## 문서 구분

- `schema.dbml`: DB 테이블, 컬럼, Enum, FK, Unique 제약조건
- `openapi.yaml`: 프론트엔드와 백엔드 사이의 HTTP API 계약
- Django 모델: 실제 서버에서 적용되는 ORM 및 마이그레이션

ERD와 OpenAPI는 역할이 다르므로 둘 다 제출하는 것이 좋다.
