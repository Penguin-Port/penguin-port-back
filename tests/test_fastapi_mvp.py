from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.main import app
from app.models import (
    AIRecommendation,
    BackendEvent,
    DailySpendBalance,
    Order,
    Product,
    RewardGrant,
    Store,
    WiFiPass,
)
from app.services import events as event_service
from app.seed import seed
from app.time import business_date, db_now


@pytest.fixture()
def client():
    init_db()
    seed(store_name="테스트 카페", username="owner", password="password")
    with TestClient(app) as test_client:
        yield test_client


def ids():
    with SessionLocal() as db:
        store = db.scalar(select(Store).where(Store.name == "테스트 카페"))
        product = db.scalar(select(Product).where(Product.store_id == store.id))
        return store.id, product.id


def create_order(
    client,
    external_id="ORDER-1",
    total=5000,
    headers=None,
    paid_at=None,
    item_unit_price=None,
    phone="010-1234-5678",
):
    store_id, product_id = ids()
    request_headers = {"X-Demo-Key": "demo-key", **(headers or {})}
    return client.post(
        "/pos/orders",
        headers=request_headers,
        json={
            "storeId": store_id,
            "externalOrderId": external_id,
            "customer": {"phone": phone},
            "items": [
                {
                    "productId": product_id,
                    "quantity": 1,
                    "unitPrice": total if item_unit_price is None else item_unit_price,
                }
            ],
            "totalAmount": total,
            "paidAt": paid_at or "2026-08-01T12:00:00+09:00",
        },
    )


def portal_session(client, order_data, phone="010-1234-5678"):
    exchange = client.post(
        "/public/order-claims/exchange",
        json={"orderClaim": order_data["orderClaim"]["token"]},
    )
    assert exchange.status_code == 200
    exchange_data = exchange.json()["data"]
    send = client.post(
        "/public/otp/send",
        json={
            "verificationTicket": exchange_data["verificationTicket"],
            "phone": phone,
        },
    )
    assert send.status_code == 201
    confirm = client.post(
        "/public/otp/confirm",
        json={"challengeId": send.json()["data"]["challengeId"], "code": "123456"},
    )
    assert confirm.status_code == 200
    return confirm.json()["data"]["portalSession"]


def test_pdf_customer_flow(client):
    order = create_order(client)
    assert order.status_code == 201
    data = order.json()["data"]
    exchange = client.post(
        "/public/order-claims/exchange",
        json={"orderClaim": data["orderClaim"]["token"]},
    )
    assert exchange.status_code == 200
    exchange_data = exchange.json()["data"]
    _, product_id = ids()
    assert exchange_data["storeName"] == "테스트 카페"
    assert exchange_data["orderNo"] == "ORDER-1"
    assert exchange_data["items"] == [
        {
            "productId": product_id,
            "name": "아메리카노",
            "quantity": 1,
            "unitPrice": 5000,
            "lineAmount": 5000,
        }
    ]
    assert exchange_data["paidAmount"] == 5000
    assert exchange_data["providedMinutes"] == 120
    send = client.post(
        "/public/otp/send",
        json={
            "verificationTicket": exchange_data["verificationTicket"],
            "phone": "010-1234-5678",
        },
    )
    assert send.status_code == 201
    confirm = client.post(
        "/public/otp/confirm",
        json={
            "challengeId": send.json()["data"]["challengeId"],
            "code": "123456",
        },
    )
    assert confirm.status_code == 200
    session = confirm.json()["data"]["portalSession"]
    pass_id = data["wifiPass"]["passId"]
    activate = client.post(
        f"/public/passes/{pass_id}/activate",
        headers={"X-Portal-Session": session},
    )
    assert activate.status_code == 200
    assert activate.json()["data"]["status"] == "ACTIVE"
    pass_response = client.get(
        f"/public/passes/{pass_id}",
        headers={"X-Portal-Session": session},
    )
    assert pass_response.status_code == 200
    assert 0 < pass_response.json()["data"]["remainingSeconds"] <= 120 * 60
    hint = client.get("/public/upsell-hint", headers={"X-Portal-Session": session})
    assert hint.status_code == 200
    options = client.get(
        f"/public/rewards/grants/{data['newRewardGrantIds'][0]}/options",
        headers={"X-Portal-Session": session},
    )
    assert options.status_code == 200
    options_data = options.json()["data"]
    assert options_data["grantId"] == data["newRewardGrantIds"][0]
    assert options_data["tierAmount"] == 5000
    assert options_data["status"] == "AWAITING_CHOICE"
    assert options_data["options"][0]["recommended"] is True
    benefit_id = options_data["options"][0]["benefitId"]
    choose = client.post(
        f"/public/rewards/{data['newRewardGrantIds'][0]}/choose",
        headers={"X-Portal-Session": session},
        json={"benefitId": benefit_id, "fulfillMode": "COUPON_7D"},
    )
    assert choose.status_code == 200
    assert choose.json()["data"]["coupon"]["status"] == "AVAILABLE"


def test_exchange_reports_additional_order_minutes(client):
    first = create_order(client, "ORDER-PROVIDED-FIRST")
    second = create_order(client, "ORDER-PROVIDED-SECOND")
    assert first.status_code == 201
    assert second.status_code == 201

    exchange = client.post(
        "/public/order-claims/exchange",
        json={"orderClaim": second.json()["data"]["orderClaim"]["token"]},
    )

    assert exchange.status_code == 200
    assert exchange.json()["data"]["providedMinutes"] == 60


def test_pdf_admin_flow_and_ai_decision(client):
    order = create_order(client, "ORDER-ADMIN")
    data = order.json()["data"]
    login = client.post("/admin/login", json={"username": "owner", "password": "password"})
    assert login.status_code == 200
    token = login.json()["data"]["accessToken"]
    auth = {"Authorization": f"Bearer {token}"}
    active = client.get("/admin/passes/active", headers=auth)
    assert active.status_code == 200
    extend = client.post(
        f"/admin/passes/{data['wifiPass']['passId']}/extend",
        headers=auth,
        json={"minutes": 15},
    )
    assert extend.status_code == 200
    expire = client.post(
        f"/admin/passes/{data['wifiPass']['passId']}/expire",
        headers=auth,
        json={},
    )
    assert expire.status_code == 200
    assert expire.json()["data"]["status"] == "EXPIRED"
    with SessionLocal() as db:
        event_types = set(
            db.scalars(
                select(BackendEvent.event_type).where(
                    BackendEvent.aggregate_id == data["wifiPass"]["passId"]
                )
            ).all()
        )
    assert "wifi.pass.extended" in event_types
    assert "wifi.pass.expired" in event_types

    recommendation = client.get("/admin/ai/recommendations", headers=auth).json()["data"][0]
    store_id, _ = ids()
    accepted = client.post(
        f"/admin/ai/recommendations/{recommendation['recommendationId']}/accept",
        headers=auth,
        json={"storeId": store_id, "version": recommendation["version"]},
    )
    assert accepted.status_code == 201


def test_admin_can_block_pass_and_publish_event(monkeypatch, client):
    monkeypatch.setattr(
        event_service,
        "settings",
        replace(event_service.settings, sse_max_seconds=0),
    )
    order = create_order(client, "ORDER-BLOCK", phone="010-9999-0001")
    data = order.json()["data"]
    pass_id = data["wifiPass"]["passId"]
    initial_version = data["wifiPass"]["version"]
    login = client.post("/admin/login", json={"username": "owner", "password": "password"})
    auth = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}

    blocked = client.post(
        f"/admin/passes/{pass_id}/block",
        headers=auth,
        json={"reason": "시연 중 관리자 차단"},
    )

    assert blocked.status_code == 200
    assert blocked.json()["data"]["status"] == "BLOCKED"
    assert blocked.json()["data"]["version"] == initial_version + 1

    repeated = client.post(f"/admin/passes/{pass_id}/block", headers=auth)
    assert repeated.status_code == 200
    assert repeated.json()["data"]["version"] == initial_version + 1

    stream = client.get("/admin/events", headers=auth)
    assert stream.status_code == 200
    assert "event: wifi.pass.blocked" in stream.text
    assert f'"passId": "{pass_id}"' in stream.text

    store_id, _ = ids()
    with SessionLocal() as db:
        event = db.scalar(
            select(BackendEvent).where(
                BackendEvent.store_id == store_id,
                BackendEvent.event_type == "wifi.pass.blocked",
                BackendEvent.aggregate_id == pass_id,
            )
        )
        assert event is not None
        assert event.payload["reason"] == "시연 중 관리자 차단"


def test_admin_sse_accepts_access_cookie(monkeypatch, client):
    monkeypatch.setattr(
        event_service,
        "settings",
        replace(event_service.settings, sse_max_seconds=0),
    )
    login = client.post("/admin/login", json={"username": "owner", "password": "password"})
    assert login.status_code == 200
    assert "smartpass_access" in client.cookies

    response = client.get(
        "/admin/events",
        headers={"Origin": "http://localhost:5173"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert ": connected" in response.text


def test_demo_key_and_seed_are_idempotent(client):
    denied = create_order(client, "ORDER-DENIED")
    assert denied.status_code == 201
    denied = client.post("/pos/orders", json={})
    assert denied.status_code in {401, 422}
    first = seed(store_name="테스트 카페", username="owner", password="password")
    second = seed(store_name="테스트 카페", username="owner", password="password")
    assert first["recommendationId"] == second["recommendationId"]


def test_expire_loop_scans_active_passes(client):
    order = create_order(client, "ORDER-EXPIRE")
    pass_id = order.json()["data"]["wifiPass"]["passId"]
    with SessionLocal() as db:
        wifi_pass = db.get(WiFiPass, pass_id)
        wifi_pass.status = "ACTIVE"
        wifi_pass.expires_at = db_now() - timedelta(minutes=1)
        db.commit()
    store_id, _ = ids()
    login = client.post("/admin/login", json={"username": "owner", "password": "password"})
    token = login.json()["data"]["accessToken"]
    active = client.get(
        "/admin/passes/active",
        params={"storeId": store_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert active.status_code == 200
    with SessionLocal() as db:
        assert db.get(WiFiPass, pass_id).status == "EXPIRED"


def test_pos_idempotency_replays_the_original_response(client):
    headers = {"Idempotency-Key": "idem-order-001"}
    first = create_order(client, "ORDER-IDEMPOTENT", headers=headers)
    second = create_order(client, "ORDER-IDEMPOTENT", headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json() == first.json()
    with SessionLocal() as db:
        orders = db.scalars(select(Order).where(Order.external_order_id == "ORDER-IDEMPOTENT")).all()
        assert len(orders) == 1

    changed = create_order(client, "ORDER-IDEMPOTENT-CHANGED", total=6000, headers=headers)
    assert changed.status_code == 409


def test_order_total_must_match_line_items(client):
    response = create_order(client, "ORDER-BAD-TOTAL", total=6000, item_unit_price=5000)
    assert response.status_code == 422


def test_business_date_uses_store_timezone_and_cutoff():
    before_cutoff = datetime(2026, 8, 2, 16, 30, tzinfo=timezone.utc)
    at_cutoff = datetime(2026, 8, 2, 17, 0, tzinfo=timezone.utc)

    assert business_date(
        before_cutoff, timezone_name="Asia/Seoul", cutoff="02:00"
    ).isoformat() == "2026-08-02"
    assert business_date(
        at_cutoff, timezone_name="Asia/Seoul", cutoff="02:00"
    ).isoformat() == "2026-08-03"


def test_phone_is_not_persisted_as_plaintext(client):
    response = create_order(client, "ORDER-PHONE-MASK")
    order_id = response.json()["data"]["orderId"]
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        assert order.phone is None
        assert order.phone_lookup_hash
        assert order.phone_last4 == "5678"


def test_coupon_can_be_listed_and_redeemed(client):
    response = create_order(client, "ORDER-COUPON-LIFECYCLE", phone="010-5555-0001")
    data = response.json()["data"]
    session = portal_session(client, data, phone="010-5555-0001")
    options = client.get(
        f"/public/rewards/grants/{data['newRewardGrantIds'][0]}/options",
        headers={"X-Portal-Session": session},
    )
    benefit_id = options.json()["data"]["options"][0]["benefitId"]
    chosen = client.post(
        f"/public/rewards/{data['newRewardGrantIds'][0]}/choose",
        headers={"X-Portal-Session": session},
        json={"benefitId": benefit_id, "fulfillMode": "COUPON_7D"},
    )
    coupon_id = chosen.json()["data"]["coupon"]["couponId"]

    listed = client.get("/public/coupons", headers={"X-Portal-Session": session})
    assert listed.status_code == 200
    assert listed.json()["data"][0]["couponId"] == coupon_id
    redeemed = client.post(
        f"/public/coupons/{coupon_id}/redeem",
        headers={"X-Portal-Session": session},
    )
    assert redeemed.status_code == 200
    assert redeemed.json()["data"]["status"] == "REDEEMED"


def test_immediate_reward_is_consumed_by_the_next_order(client):
    first = create_order(client, "ORDER-IMMEDIATE-FIRST", phone="010-5555-0002")
    first_data = first.json()["data"]
    session = portal_session(client, first_data, phone="010-5555-0002")
    options = client.get(
        f"/public/rewards/grants/{first_data['newRewardGrantIds'][0]}/options",
        headers={"X-Portal-Session": session},
    )
    benefit_id = options.json()["data"]["options"][0]["benefitId"]
    chosen = client.post(
        f"/public/rewards/{first_data['newRewardGrantIds'][0]}/choose",
        headers={"X-Portal-Session": session},
        json={"benefitId": benefit_id, "fulfillMode": "IMMEDIATE"},
    )
    assert chosen.status_code == 200
    assert chosen.json()["data"]["immediate"]["status"] == "AVAILABLE"

    second = create_order(client, "ORDER-IMMEDIATE-SECOND", phone="010-5555-0002")
    assert second.status_code == 201
    assert second.json()["data"]["appliedRewards"][0]["status"] == "CONSUMED"


def test_admin_refresh_rotates_and_logout_revokes_refresh_token(client):
    login = client.post("/admin/login", json={"username": "owner", "password": "password"})
    assert login.status_code == 200
    first_tokens = login.json()["data"]
    assert first_tokens["role"] == "OWNER"
    refreshed = client.post(
        "/admin/refresh", json={"refreshToken": first_tokens["refreshToken"]}
    )
    assert refreshed.status_code == 200
    second_tokens = refreshed.json()["data"]
    assert second_tokens["refreshToken"] != first_tokens["refreshToken"]
    reused = client.post(
        "/admin/refresh", json={"refreshToken": first_tokens["refreshToken"]}
    )
    assert reused.status_code == 401
    me = client.get(
        "/admin/me",
        headers={"Authorization": f"Bearer {second_tokens['accessToken']}"},
    )
    assert me.status_code == 200
    assert me.json()["data"]["role"] == "OWNER"
    logged_out = client.post(
        "/admin/logout", json={"refreshToken": second_tokens["refreshToken"]}
    )
    assert logged_out.status_code == 200
    assert (
        client.post(
            "/admin/refresh", json={"refreshToken": second_tokens["refreshToken"]}
        ).status_code
        == 401
    )


def test_owner_can_manage_fastapi_admin_team(client):
    store_id, _ = ids()
    login = client.post("/admin/login", json={"username": "owner", "password": "password"})
    auth = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
    listed = client.get("/admin/team", headers=auth)
    assert listed.status_code == 200
    assert listed.json()["data"][0]["role"] == "OWNER"

    created = client.post(
        "/admin/team",
        headers=auth,
        json={
            "storeId": store_id,
            "username": "team-staff",
            "password": "team-password",
            "role": "STAFF",
        },
    )
    assert created.status_code == 201
    member = created.json()["data"]
    assert member["isActive"] is True

    updated = client.patch(
        f"/admin/team/{member['adminId']}",
        headers=auth,
        json={"storeId": store_id, "role": "MANAGER", "isActive": False},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["isActive"] is False
    assert client.post(
        "/admin/login",
        json={"username": "team-staff", "password": "team-password"},
    ).status_code == 401


def test_admin_can_simulate_and_publish_wifi_policy(client):
    login = client.post("/admin/login", json={"username": "owner", "password": "password"})
    token = login.json()["data"]["accessToken"]
    auth = {"Authorization": f"Bearer {token}"}
    current = client.get("/admin/wifi/policies", headers=auth)
    assert current.status_code == 200
    version = current.json()["data"]["version"]
    simulated = client.post(
        "/admin/wifi/policies/simulate",
        headers=auth,
        json={"orderType": "FIRST", "amount": 10000},
    )
    assert simulated.status_code == 200
    assert simulated.json()["data"]["minutes"] == 150
    published = client.post(
        "/admin/wifi/policies/publish",
        headers=auth,
        json={
            "version": version,
            "baseMinutes": 90,
            "firstOrderTiers": [{"minAmount": 10000, "minutes": 20}],
            "additionalOrderTiers": [{"minAmount": 5000, "minutes": 45}],
        },
    )
    assert published.status_code == 200
    assert published.json()["data"]["version"] == version + 1
    after = client.post(
        "/admin/wifi/policies/simulate",
        headers=auth,
        json={"orderType": "FIRST", "amount": 10000},
    )
    assert after.json()["data"]["minutes"] == 110


def test_admin_can_edit_then_accept_ai_recommendation(client):
    seeded = seed(
        store_name="추천 수정 카페",
        username="editor",
        password="password",
    )
    login = client.post("/admin/login", json={"username": "editor", "password": "password"})
    auth = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
    recommendation = client.get("/admin/ai/recommendations", headers=auth).json()["data"][0]
    starts = datetime.now(timezone.utc) + timedelta(hours=1)
    ends = starts + timedelta(hours=2)
    edited = client.patch(
        f"/admin/ai/recommendations/{recommendation['recommendationId']}",
        headers=auth,
        json={
            "storeId": seeded["storeId"],
            "version": recommendation["version"],
            "menuIds": [seeded["productId"]],
            "discountRate": 20,
            "startsAt": starts.isoformat(),
            "endsAt": ends.isoformat(),
        },
    )
    assert edited.status_code == 200
    edited_data = edited.json()["data"]
    accepted = client.post(
        f"/admin/ai/recommendations/{recommendation['recommendationId']}/accept",
        headers=auth,
        json={"storeId": seeded["storeId"], "version": edited_data["version"]},
    )
    assert accepted.status_code == 201
    assert accepted.json()["data"]["status"] == "ACCEPTED"


def test_partial_and_full_refund_roll_back_daily_spend_and_revoke_unused_grants(client):
    order = create_order(
        client,
        "ORDER-REFUND",
        total=10000,
        item_unit_price=10000,
        phone="010-5555-0003",
    )
    order_id = order.json()["data"]["orderId"]
    store_id, _ = ids()
    first_refund = client.post(
        f"/pos/orders/{order_id}/refund",
        headers={"X-Demo-Key": "demo-key", "Idempotency-Key": "refund-001"},
        json={"storeId": store_id, "refundAmount": 5000},
    )
    replay = client.post(
        f"/pos/orders/{order_id}/refund",
        headers={"X-Demo-Key": "demo-key", "Idempotency-Key": "refund-001"},
        json={"storeId": store_id, "refundAmount": 5000},
    )
    assert first_refund.status_code == 200
    assert replay.json() == first_refund.json()
    assert first_refund.json()["data"]["status"] == "PARTIALLY_REFUNDED"
    with SessionLocal() as db:
        saved_order = db.get(Order, order_id)
        balance = db.scalar(
            select(DailySpendBalance).where(
                DailySpendBalance.store_id == store_id,
                DailySpendBalance.customer_key == saved_order.customer_key,
                DailySpendBalance.business_date == saved_order.business_date,
            )
        )
        grants = db.scalars(select(RewardGrant).where(RewardGrant.store_id == store_id)).all()
        assert balance is not None
        assert balance.total_amount == 5000
        assert any(grant.status == "REVOKED" for grant in grants)

    full_refund = client.post(
        f"/pos/orders/{order_id}/refund",
        headers={"X-Demo-Key": "demo-key"},
        json={"storeId": store_id, "refundAmount": 5000},
    )
    assert full_refund.status_code == 200
    assert full_refund.json()["data"]["status"] == "REFUNDED"


def test_privacy_notice_and_problem_error_contract(client):
    store_id, _ = ids()
    privacy = client.get(f"/public/stores/{store_id}/privacy-notice")
    assert privacy.status_code == 200
    assert privacy.json()["data"]["automaticDeletion"] is True
    invalid = client.post("/pos/orders", json={})
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "REQUEST_VALIDATION_ERROR"
    assert "requestId" in invalid.json()


def test_admin_sales_summary_and_inventory_risk_recommendation(client):
    login = client.post("/admin/login", json={"username": "owner", "password": "password"})
    auth = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
    summary = client.get("/admin/ai/sales-summary", headers=auth)
    assert summary.status_code == 200
    assert "totalSales" in summary.json()["data"]
    assert summary.json()["data"]["recommendation"]["recommendationId"]

    store_id, product_id = ids()
    inventory = client.post(
        "/admin/inventory",
        headers=auth,
        json={
            "storeId": store_id,
            "productId": product_id,
            "quantity": 0,
            "lowStockThreshold": 1,
            "expiresOn": "2026-08-02",
        },
    )
    assert inventory.status_code == 200
    scan = client.post("/admin/inventory/scan", headers=auth)
    assert scan.status_code == 200
    assert scan.json()["data"][0]["type"] == "INVENTORY_PROMOTION"
    listed = client.get("/admin/ai/inventory", headers=auth)
    assert listed.status_code == 200
    assert listed.json()["data"][0]["evidence"]["riskScore"] == 100


def test_admin_can_generate_time_sale_recommendation_with_fallback(client):
    login = client.post("/admin/login", json={"username": "owner", "password": "password"})
    auth = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
    store_id, product_id = ids()

    generated = client.post(
        "/admin/ai/recommendations/generate",
        headers=auth,
        json={"storeId": store_id, "type": "TIME_SALE"},
    )

    assert generated.status_code == 201
    item = generated.json()["data"][0]
    assert item["type"] == "TIME_SALE"
    assert item["status"] == "PENDING"
    assert item["payload"]["source"] == "RULE_FALLBACK"
    assert item["payload"]["menuIds"] == [product_id]
    assert item["evidence"]["provider"] == "RULE_FALLBACK"


def test_frontend_reference_aliases_are_available(client):
    order = create_order(client, "ORDER-ALIAS", phone="010-5555-0004")
    data = order.json()["data"]
    session = portal_session(client, data, phone="010-5555-0004")
    hint = client.get(
        "/public/kiosk/upsell-hint", headers={"X-Portal-Session": session}
    )
    assert hint.status_code == 200


def test_otp_send_is_rate_limited_and_reward_tiers_are_auditable(client):
    order = create_order(client, "ORDER-OTP-RATE", phone="010-5555-0005")
    claim = client.post(
        "/public/order-claims/exchange",
        json={"orderClaim": order.json()["data"]["orderClaim"]["token"]},
    )
    ticket = claim.json()["data"]["verificationTicket"]
    for _ in range(3):
        assert (
            client.post(
                "/public/otp/send",
                json={"verificationTicket": ticket, "phone": "010-5555-0005"},
            ).status_code
            == 201
        )
    limited = client.post(
        "/public/otp/send",
        json={"verificationTicket": ticket, "phone": "010-5555-0005"},
    )
    assert limited.status_code == 429

    login = client.post("/admin/login", json={"username": "owner", "password": "password"})
    auth = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
    store_id, _ = ids()
    tiers = client.get("/admin/rewards/tiers", headers=auth)
    assert tiers.status_code == 200
    tier = tiers.json()["data"][0]
    updated = client.post(
        "/admin/rewards/tiers",
        headers=auth,
        json={
            "storeId": store_id,
            "tierId": tier["tierId"],
            "name": tier["name"],
            "thresholdAmount": tier["thresholdAmount"],
            "sortOrder": tier["sortOrder"],
            "benefits": tier["benefits"],
        },
    )
    assert updated.status_code == 200
    audit = client.get("/admin/audit", headers=auth)
    assert audit.status_code == 200
    assert any(item["action"] == "REWARD_TIER_UPDATED" for item in audit.json()["data"])
