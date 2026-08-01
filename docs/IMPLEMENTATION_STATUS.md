# Smart WiFi Pass 구현 현황

기준 문서:

- `Smart_WiFi_Pass_Backend_Spec.pdf`
- `Smart_WiFi_Pass_Frontend_Spec.pdf`

축소판 MVP 명세의 시연 경로(`/public/otp/*`, `/public/upsell-hint`,
`/public/rewards/{grantId}/choose`, `/admin/login`, `/admin/passes/*`)도
기존 확장 API와 함께 제공한다. 기존 Django 구현을 유지하면서 PDF의 짧은 시연 흐름을
그대로 호출할 수 있도록 호환 경로, 멱등 시드 명령, 직접 만료 스캔을 추가했다.

상태 정의:

- **완료**: Django 모델, 서비스 함수, API 또는 Worker가 연결되어 자동 테스트 가능
- **Demo**: 외부 사업자 없이 로컬에서 동작하며 Provider 교체 지점이 존재
- **외부 연결**: 코드 계약은 존재하지만 운영 자격증명과 실제 장비가 필요

## 주문·인증

| 요구사항 | 상태 | 구현 |
|---|---|---|
| POS 주문 등록 | 완료 | `POST /api/v1/pos/orders` |
| HMAC timestamp·nonce·body | 완료 | `PosHMACPermission` |
| Idempotency-Key | 완료 | 요청 해시와 원 응답 저장·재사용 |
| QR Order Claim | 완료 | 10분 만료, 1회 교환 |
| 전화번호 OTP | Demo | 해시 저장, 3분 만료, 5회 제한, `demo_messages` Inbox |
| PDF OTP 경로 | 완료 | `/public/otp/send`, `/public/otp/confirm` 별칭 |
| Portal Session | 완료 | 서명된 24시간 Session |
| 부분·전체 환불 | 완료 | 누적 금액 롤백 및 미사용 혜택 회수 |

## 당일 누적 리워드

| 요구사항 | 상태 | 구현 |
|---|---|---|
| 매장 Timezone 영업일 | 완료 | 영업일 cutoff 반영 |
| 5천·1만·2만 티어 | 완료 | 매장별 동적 티어 |
| 동일 티어 중복 지급 방지 | 완료 | DB Unique + Transaction |
| AI 혜택 순위 | Demo | 활성 혜택 순서 폴백 |
| 즉시 혜택 | 완료 | Grant 즉시 확정, Wi-Fi 종일권 즉시 반영 |
| 7일 쿠폰 | 완료 | 발급·목록·사용·만료 검사 |
| 업셀 힌트 | 완료 | 다음 티어와 남은 금액 계산 |
| PDF 업셀·리워드 경로 | 완료 | `/public/upsell-hint`, `/public/rewards/{grantId}/choose` |

## Wi-Fi

| 요구사항 | 상태 | 구현 |
|---|---|---|
| 첫 주문 기본 시간 | 완료 | `base_minutes` |
| 첫 주문 금액 보너스 | 완료 | `FIRST` 금액 구간 |
| 추가 주문 연장 | 완료 | `ADDITIONAL` 금액 구간 |
| Quiet Hours | 완료 | 설정 시각까지 자동 연장 |
| 종일권 | 완료 | 영업일 종료 시각 적용 |
| 상태 전이 | 완료 | 발급·활성·만료·차단·실패 |
| 오만료 방지 | 완료 | `pass_version` 불일치 Worker 스킵 |
| 만료·알림·접속해제 | 완료 | ScheduledAction + Celery Beat |
| PDF 직접 만료 루프 | 완료 | 활성/임박 이용권 `expires_at` 직접 스캔 + Demo revoke |
| 정책 스냅샷 | 완료 | 이용권 발급·연장 시 `policy_snapshot` 저장 |
| 실제 네트워크 | Demo | `DemoNetworkAdapter` |
| UniFi/RADIUS/MikroTik | 외부 연결 | `NetworkAdapter` 구현 교체 필요 |

## AI·매출·프로모션

| 요구사항 | 상태 | 구현 |
|---|---|---|
| 시간대 매출 집계 | 완료 | `AnalyticsHourly` |
| AI 매출 요약 | Demo | 규칙 기반 비식별 폴백 |
| 타임세일 추천 | Demo | 저주문 시간대 규칙 추천 |
| 재고 프로모션 | 완료 | 유통기한·수량 위험 점수 |
| 신메뉴 트렌드 | Demo | 외부 API 장애용 템플릿 |
| 추천 수정·승인·거절 | 완료 | 버전 충돌 검사 |
| 승인 후 프로모션 | 완료 | Recommendation과 1:1 생성 |
| OpenAI JSON Schema | 외부 연결 | `AIProvider` 교체 및 API Key 필요 |

## 운영·보안

| 요구사항 | 상태 | 구현 |
|---|---|---|
| Admin 로그인·갱신·로그아웃 | 완료 | JWT Access + HttpOnly Refresh Rotation + Session 호환 |
| PDF Admin 경로 | 완료 | `/admin/login`, `/admin/passes/active`, `/admin/passes/{id}/extend`, `/admin/passes/{id}/expire` |
| OWNER/MANAGER/STAFF/VIEWER | 완료 | 매장 Membership RBAC |
| Audit Log | 완료 | 환불·정책·설정·수동 이용권 작업 |
| 개인정보 안내·보존 | 완료 | 매장별 보존 정책 |
| 개인정보 자동 폐기 | 완료 | Daily Worker |
| 점주 보호 Export | 완료 | 최근 주문·인증 증빙 JSON |
| Rate Limit | 완료 | POS·OTP·Public Scope |
| Argon2id | 완료 | `argon2-cffi` 설치 시 최우선 Hasher |
| 전화번호 보호 | 완료 | Fernet ciphertext + HMAC lookup hash + last4 |
| SSE | 완료 | Outbox 기반 Event Stream 및 Last-Event-ID |
| Redis | 완료 | 캐시·nonce·Throttle 공유 |
| Docker Compose | 완료 | Django, PostgreSQL, Redis, Worker, Beat |

## 산출물

| 파일 | 역할 |
|---|---|
| `docs/openapi.yaml` | 47개 경로, 54개 HTTP Operation |
| `docs/schema.dbml` | dbdiagram.io ERD |
| `scripts/generate_openapi.py` | OpenAPI 재생성 |
| `api/tests.py` | 핵심 통합 흐름과 명세 누락 검사 |
| `api/management/commands/seed_mvp.py` | PDF 축소 MVP 데모 매장·메뉴·정책·리워드·AI 카드 시드 |
