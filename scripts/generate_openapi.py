from pathlib import Path

import yaml


OPERATIONS = [
    ("POST", "/pos/orders", "POS", "결제 완료 주문 생성", "PaidOrderRequest", "pos"),
    ("POST", "/pos/rewards/immediate/{redemptionId}/apply", "POS", "즉시 리워드 주문 적용", "ImmediateBenefitApplyRequest", "pos"),
    ("POST", "/public/order-claims/exchange", "Public", "QR 주문 Claim 교환", "OrderClaimExchangeRequest", None),
    ("POST", "/public/verifications/start", "Public", "OTP 인증 시작", "VerificationStartRequest", None),
    ("POST", "/public/verifications/confirm", "Public", "OTP 인증 확인", "VerificationConfirmRequest", None),
    ("GET", "/public/passes/{passId}", "Public", "Wi-Fi 이용권 조회", None, "portal"),
    ("POST", "/public/passes/{passId}/activate", "Public", "Wi-Fi 이용권 활성화", None, "portal"),
    ("GET", "/public/kiosk/upsell-hint", "Public", "다음 리워드 업셀 정보", None, "portal"),
    ("GET", "/public/rewards/grants/{grantId}/options", "Rewards", "리워드 선택지 조회", None, "portal"),
    ("POST", "/public/rewards/grants/{grantId}/choose", "Rewards", "리워드 혜택 선택", "RewardChooseRequest", "portal"),
    ("GET", "/public/coupons", "Rewards", "쿠폰함 조회", None, "portal"),
    ("POST", "/public/coupons/{couponId}/redeem", "Rewards", "쿠폰 사용", None, "portal"),
    ("GET", "/public/stores/{storeId}/privacy-notice", "Public", "개인정보 처리 안내 조회", None, None),
    ("POST", "/admin/auth/login", "Admin Auth", "관리자 로그인", "LoginRequest", None),
    ("POST", "/admin/auth/refresh", "Admin Auth", "관리자 Refresh Token 회전", None, "refresh"),
    ("POST", "/admin/auth/logout", "Admin Auth", "관리자 로그아웃", None, "admin"),
    ("GET", "/admin/auth/me", "Admin Auth", "현재 관리자 정보", None, "admin"),
    ("GET", "/admin/orders", "Admin Order", "주문 목록 조회", None, "admin"),
    ("POST", "/admin/orders/{orderId}/refund", "Admin Order", "주문 환불", "RefundRequest", "admin"),
    ("GET", "/admin/catalog/products", "Admin Catalog", "메뉴 목록 조회", None, "admin"),
    ("POST", "/admin/catalog/products", "Admin Catalog", "메뉴 생성", "ProductCreateRequest", "admin"),
    ("GET", "/admin/wifi/policies", "Admin WiFi", "Wi-Fi 정책 목록", None, "admin"),
    ("POST", "/admin/wifi/policies", "Admin WiFi", "Wi-Fi 정책 버전 생성", "WiFiPolicyRequest", "admin"),
    ("POST", "/admin/wifi/policies/simulate", "Admin WiFi", "Wi-Fi 정책 계산 미리보기", "WiFiSimulateRequest", "admin"),
    ("POST", "/admin/wifi/policies/{policyId}/publish", "Admin WiFi", "Wi-Fi 정책 게시", None, "admin"),
    ("GET", "/admin/wifi/live-passes", "Admin WiFi", "실시간 이용권 목록", None, "admin"),
    ("GET", "/admin/wifi/passes/{passId}/extensions", "Admin WiFi", "이용권 연장 이력", None, "admin"),
    ("POST", "/admin/wifi/passes/{passId}/actions", "Admin WiFi", "이용권 연장·차단·해제", "ManualPassActionRequest", "admin"),
    ("GET", "/admin/rewards/tiers", "Admin Rewards", "리워드 티어 목록", None, "admin"),
    ("POST", "/admin/rewards/tiers", "Admin Rewards", "리워드 티어 생성", "RewardTierRequest", "admin"),
    ("GET", "/admin/rewards/history", "Admin Rewards", "리워드 지급 이력", None, "admin"),
    ("GET", "/admin/inventory", "Admin Inventory", "재고 목록", None, "admin"),
    ("POST", "/admin/inventory", "Admin Inventory", "재고 품목 생성", "InventoryCreateRequest", "admin"),
    ("POST", "/admin/inventory/{itemId}/adjust", "Admin Inventory", "재고 수량 조정", "InventoryAdjustRequest", "admin"),
    ("POST", "/admin/inventory/scan", "Admin Inventory", "재고 위험 스캔", "StoreRequest", "admin"),
    ("GET", "/admin/ai/recommendations", "Admin AI", "AI 추천 목록", None, "admin"),
    ("POST", "/admin/ai/recommendations/generate", "Admin AI", "AI 추천 생성", "RecommendationGenerateRequest", "admin"),
    ("PATCH", "/admin/ai/recommendations/{recommendationId}", "Admin AI", "AI 추천 수정", "RecommendationEditRequest", "admin"),
    ("POST", "/admin/ai/recommendations/{recommendationId}/accept", "Admin AI", "AI 추천 승인 및 프로모션 생성", "RecommendationAcceptRequest", "admin"),
    ("POST", "/admin/ai/recommendations/{recommendationId}/reject", "Admin AI", "AI 추천 거절", "RecommendationRejectRequest", "admin"),
    ("GET", "/admin/promotions", "Admin Promotion", "프로모션 목록", None, "admin"),
    ("PATCH", "/admin/promotions/{promotionId}", "Admin Promotion", "프로모션 수정", "PromotionUpdateRequest", "admin"),
    ("GET", "/admin/analytics/hourly", "Admin Analytics", "시간대별 운영 지표", None, "admin"),
    ("GET", "/admin/audit", "Admin Operations", "감사 로그 목록", None, "admin"),
    ("GET", "/admin/notifications", "Admin Operations", "알림 발송 이력", None, "admin"),
    ("GET", "/admin/anomalies", "Admin Operations", "운영 이상 현황", None, "admin"),
    ("GET", "/admin/team", "Admin Team", "매장 팀 목록", None, "admin"),
    ("POST", "/admin/team", "Admin Team", "매장 팀원 역할 등록·수정", "MembershipRequest", "admin"),
    ("GET", "/admin/privacy/retention", "Admin Privacy", "개인정보 보존 정책 조회", None, "admin"),
    ("PATCH", "/admin/privacy/retention", "Admin Privacy", "개인정보 보존 정책 수정", "PrivacyRetentionRequest", "admin"),
    ("GET", "/admin/privacy/export", "Admin Privacy", "점주 보호 증빙 내보내기", None, "admin"),
    ("GET", "/admin/settings", "Admin Settings", "매장 설정 조회", None, "admin"),
    ("PATCH", "/admin/settings", "Admin Settings", "매장 설정 수정", "StoreSettingsRequest", "admin"),
    ("GET", "/stores/{storeId}/events", "Realtime", "매장 SSE 이벤트", None, "admin"),
]


def object_schema(properties, required=None):
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


uuid_schema = {"type": "string", "format": "uuid"}
date_time_schema = {"type": "string", "format": "date-time"}
store_id = {"storeId": uuid_schema}

schemas = {
    "SuccessEnvelope": object_schema(
        {
            "data": {},
            "meta": object_schema(
                {"requestId": {"type": "string"}, "serverTime": date_time_schema},
                ["requestId", "serverTime"],
            ),
        },
        ["data", "meta"],
    ),
    "Problem": object_schema(
        {
            "type": {"type": "string", "format": "uri"},
            "title": {"type": "string"},
            "status": {"type": "integer"},
            "code": {"type": "string"},
            "detail": {"type": "string"},
            "retryable": {"type": "boolean"},
            "requestId": {"type": "string"},
        },
        ["type", "title", "status", "code", "detail", "retryable", "requestId"],
    ),
    "PaidOrderRequest": object_schema(
        {
            **store_id,
            "externalOrderId": {"type": "string"},
            "customer": object_schema(
                {
                    "memberId": {"type": ["string", "null"]},
                    "phone": {"type": ["string", "null"], "example": "01012345678"},
                }
            ),
            "items": {
                "type": "array",
                "minItems": 1,
                "items": object_schema(
                    {
                        "productId": uuid_schema,
                        "quantity": {"type": "integer", "minimum": 1},
                        "unitPrice": {"type": "integer", "minimum": 0},
                    },
                    ["productId", "quantity"],
                ),
            },
            "totalAmount": {"type": "integer", "minimum": 0},
            "paidAt": date_time_schema,
        },
        ["storeId", "externalOrderId", "customer", "items", "totalAmount", "paidAt"],
    ),
    "OrderClaimExchangeRequest": object_schema(
        {"orderClaim": {"type": "string"}}, ["orderClaim"]
    ),
    "ImmediateBenefitApplyRequest": object_schema(
        {**store_id, "orderId": uuid_schema}, ["storeId", "orderId"]
    ),
    "VerificationStartRequest": object_schema(
        {"verificationTicket": {"type": "string"}, "phone": {"type": "string"}},
        ["verificationTicket", "phone"],
    ),
    "VerificationConfirmRequest": object_schema(
        {
            "challengeId": uuid_schema,
            "code": {"type": "string", "pattern": "^\\d{6}$"},
        },
        ["challengeId", "code"],
    ),
    "RewardChooseRequest": object_schema(
        {
            "benefitId": uuid_schema,
            "fulfillMode": {"enum": ["IMMEDIATE", "COUPON_7D"]},
        },
        ["benefitId", "fulfillMode"],
    ),
    "LoginRequest": object_schema(
        {"username": {"type": "string"}, "password": {"type": "string"}},
        ["username", "password"],
    ),
    "StoreRequest": object_schema(store_id, ["storeId"]),
    "RefundRequest": object_schema(
        {**store_id, "refundAmount": {"type": "integer", "minimum": 1}},
        ["storeId", "refundAmount"],
    ),
    "ProductCreateRequest": object_schema(
        {
            **store_id,
            "categoryId": uuid_schema,
            "name": {"type": "string"},
            "price": {"type": "integer", "minimum": 0},
        },
        ["storeId", "categoryId", "name", "price"],
    ),
    "WiFiPolicyRequest": object_schema(
        {
            **store_id,
            "baseMinutes": {"type": "integer", "minimum": 1},
            "quietHoursEnabled": {"type": "boolean"},
            "quietHoursUntil": {"type": ["string", "null"], "format": "time"},
            "amountTiers": {
                "type": "array",
                "items": object_schema(
                    {
                        "orderType": {"enum": ["FIRST", "ADDITIONAL"]},
                        "minAmount": {"type": "integer", "minimum": 0},
                        "bonusMinutes": {"type": "integer", "minimum": 0},
                    },
                    ["orderType", "minAmount", "bonusMinutes"],
                ),
            },
        },
        ["storeId", "baseMinutes", "amountTiers"],
    ),
    "WiFiSimulateRequest": object_schema(
        {
            **store_id,
            "orderType": {"enum": ["FIRST", "ADDITIONAL"]},
            "amount": {"type": "integer", "minimum": 0},
        },
        ["storeId", "orderType", "amount"],
    ),
    "ManualPassActionRequest": object_schema(
        {
            **store_id,
            "action": {"enum": ["EXTEND", "BLOCK", "UNBLOCK"]},
            "minutes": {"type": "integer", "minimum": 1},
        },
        ["storeId", "action"],
    ),
    "RewardTierRequest": object_schema(
        {
            **store_id,
            "name": {"type": "string"},
            "thresholdAmount": {"type": "integer", "minimum": 1},
            "sortOrder": {"type": "integer", "minimum": 0},
            "benefits": {
                "type": "array",
                "items": object_schema(
                    {
                        "benefitType": {"type": "string"},
                        "title": {"type": "string"},
                        "payload": {"type": "object"},
                    },
                    ["benefitType", "title"],
                ),
            },
        },
        ["storeId", "name", "thresholdAmount", "sortOrder", "benefits"],
    ),
    "InventoryCreateRequest": object_schema(
        {
            **store_id,
            "productId": uuid_schema,
            "quantity": {"type": "number"},
            "unit": {"type": "string"},
            "lowStockThreshold": {"type": "number"},
            "expiresOn": {"type": ["string", "null"], "format": "date"},
        },
        ["storeId", "productId", "quantity"],
    ),
    "InventoryAdjustRequest": object_schema(
        {"quantityDelta": {"type": "number"}, "reason": {"type": "string"}},
        ["quantityDelta", "reason"],
    ),
    "RecommendationGenerateRequest": object_schema(
        {
            **store_id,
            "type": {
                "enum": [
                    "SALES_SUMMARY",
                    "TIME_SALE",
                    "INVENTORY_PROMOTION",
                    "MENU_TREND",
                ]
            },
        },
        ["storeId", "type"],
    ),
    "RecommendationEditRequest": object_schema(
        {
            **store_id,
            "version": {"type": "integer", "minimum": 1},
            "payload": {"type": "object"},
            "reason": {"type": "string"},
        },
        ["storeId", "version", "payload"],
    ),
    "RecommendationAcceptRequest": object_schema(
        {
            **store_id,
            "version": {"type": "integer", "minimum": 1},
            "title": {"type": "string"},
            "payload": {"type": "object"},
            "startsAt": date_time_schema,
            "endsAt": date_time_schema,
        },
        ["storeId", "version", "title", "payload", "startsAt", "endsAt"],
    ),
    "RecommendationRejectRequest": object_schema(
        {**store_id, "reason": {"type": "string"}}, ["storeId"]
    ),
    "PromotionUpdateRequest": object_schema(
        {
            **store_id,
            "title": {"type": "string"},
            "payload": {"type": "object"},
            "startsAt": date_time_schema,
            "endsAt": date_time_schema,
            "status": {"enum": ["DRAFT", "SCHEDULED", "ACTIVE", "ENDED"]},
        },
        ["storeId"],
    ),
    "MembershipRequest": object_schema(
        {
            **store_id,
            "userId": {"type": "integer"},
            "role": {"enum": ["OWNER", "MANAGER", "STAFF", "VIEWER"]},
        },
        ["storeId", "userId", "role"],
    ),
    "PrivacyRetentionRequest": object_schema(
        {
            **store_id,
            "phoneRetentionDays": {"type": "integer", "minimum": 1},
            "verificationRetentionDays": {"type": "integer", "minimum": 1},
            "auditRetentionDays": {"type": "integer", "minimum": 1},
            "noticeText": {"type": "string"},
        },
        ["storeId"],
    ),
    "StoreSettingsRequest": object_schema(
        {
            **store_id,
            "name": {"type": "string"},
            "timezone": {"type": "string"},
            "businessDayCutoff": {"type": "string", "format": "time"},
            "segment": {"enum": ["UNIVERSITY", "FRANCHISE"]},
            "otpSkipEnabled": {"type": "boolean"},
        },
        ["storeId"],
    ),
}


def path_parameters(path):
    parameters = []
    for name in ["passId", "grantId", "couponId", "storeId", "orderId", "policyId", "itemId", "recommendationId", "promotionId", "redemptionId"]:
        if "{" + name + "}" in path:
            parameters.append(
                {
                    "name": name,
                    "in": "path",
                    "required": True,
                    "schema": (
                        {"type": "integer"}
                        if name == "policyId"
                        else {"type": "string", "format": "uuid"}
                    ),
                }
            )
    return parameters


def make_operation(method, path, tag, summary, request_schema, security):
    operation = {
        "tags": [tag],
        "summary": summary,
        "operationId": "".join(
            [
                method.lower(),
                *[
                    part.replace("{", "").replace("}", "").title()
                    for part in path.split("/")
                    if part
                ],
            ]
        ),
        "responses": {
            "200": {
                "description": "성공",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/SuccessEnvelope"}
                    }
                },
            },
            "400": {"$ref": "#/components/responses/BadRequest"},
            "401": {"$ref": "#/components/responses/Unauthorized"},
            "403": {"$ref": "#/components/responses/Forbidden"},
            "404": {"$ref": "#/components/responses/NotFound"},
            "409": {"$ref": "#/components/responses/Conflict"},
            "422": {"$ref": "#/components/responses/RuleViolation"},
        },
    }
    if method == "POST":
        operation["responses"]["201"] = operation["responses"]["200"]
    parameters = path_parameters(path)
    if parameters:
        operation["parameters"] = parameters
    if request_schema:
        operation["requestBody"] = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": f"#/components/schemas/{request_schema}"}
                }
            },
        }
    if security == "portal":
        operation["security"] = [{"portalSession": []}]
    elif security == "admin":
        operation["security"] = [{"bearerAuth": []}, {"sessionAuth": []}]
    elif security == "refresh":
        operation["security"] = [{"refreshCookie": []}]
    elif security == "pos":
        operation["security"] = [{"posSignature": []}]
        operation.setdefault("parameters", []).extend(
            [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string"},
                },
                {
                    "name": "X-Timestamp",
                    "in": "header",
                    "required": False,
                    "schema": {"type": "string", "format": "date-time"},
                    "description": "POS_HMAC_SECRET 설정 시 필수",
                },
                {
                    "name": "X-Nonce",
                    "in": "header",
                    "required": False,
                    "schema": {"type": "string"},
                },
            ]
        )
    else:
        operation["security"] = []
    if method == "GET" and path.startswith("/admin/") and "{" not in path:
        operation.setdefault("parameters", []).append(
            {
                "name": "storeId",
                "in": "query",
                "required": True,
                "schema": uuid_schema,
            }
        )
    if path == "/stores/{storeId}/events":
        operation["responses"]["200"] = {
            "description": "SSE event stream",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    return operation


paths = {}
for method, path, tag, summary, request_schema, security in OPERATIONS:
    paths.setdefault(path, {})[method.lower()] = make_operation(
        method, path, tag, summary, request_schema, security
    )


problem_content = {
    "application/json": {"schema": {"$ref": "#/components/schemas/Problem"}}
}
spec = {
    "openapi": "3.1.0",
    "info": {
        "title": "Smart WiFi Pass API",
        "version": "1.0.0",
        "description": (
            "Django REST API 전체 계약. 외부 SMS, OpenAI, Network Adapter는 "
            "Provider 인터페이스와 Demo/규칙 기반 폴백으로 동작한다."
        ),
    },
    "servers": [{"url": "http://localhost:8000/api/v1", "description": "Local"}],
    "paths": paths,
    "components": {
        "securitySchemes": {
            "portalSession": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Portal-Session",
            },
            "sessionAuth": {"type": "apiKey", "in": "cookie", "name": "sessionid"},
            "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
            "refreshCookie": {
                "type": "apiKey",
                "in": "cookie",
                "name": "smartpass_refresh",
            },
            "posSignature": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Signature",
            },
        },
        "schemas": schemas,
        "responses": {
            "BadRequest": {
                "description": "요청 형식 오류",
                "content": problem_content,
            },
            "Unauthorized": {"description": "인증 실패", "content": problem_content},
            "Forbidden": {"description": "권한 부족", "content": problem_content},
            "NotFound": {"description": "리소스 없음", "content": problem_content},
            "Conflict": {"description": "상태 또는 버전 충돌", "content": problem_content},
            "RuleViolation": {"description": "도메인 규칙 위반", "content": problem_content},
        },
    },
}


output = Path(__file__).resolve().parent.parent / "docs" / "openapi.yaml"
output.write_text(
    yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=100),
    encoding="utf-8",
)
print(f"Wrote {output} with {len(paths)} paths and {len(OPERATIONS)} operations")
