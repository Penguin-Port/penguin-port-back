from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.main import app
from app.models import AIRecommendation, Order, Product, Store, WiFiPass
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

    recommendation = client.get("/admin/ai/recommendations", headers=auth).json()["data"][0]
    store_id, _ = ids()
    accepted = client.post(
        f"/admin/ai/recommendations/{recommendation['recommendationId']}/accept",
        headers=auth,
        json={"storeId": store_id, "version": recommendation["version"]},
    )
    assert accepted.status_code == 201


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
