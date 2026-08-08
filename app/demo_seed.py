import argparse
from datetime import timedelta

from sqlalchemy import select

from app.db import init_db, session_scope
from app.models import InventoryEvent, InventoryItem, Order, OrderItem, Product, Store, WiFiPass
from app.seed import seed
from app.services.analytics import sales_summary
from app.services.inventory import calculate_risk_score
from app.time import business_date, db_now


DEMO_ORDERS = (
    ("DEMO-ORDER-001", "demo-customer-001", 5000, 5),
    ("DEMO-ORDER-002", "demo-customer-002", 10000, 4),
    ("DEMO-ORDER-003", "demo-customer-003", 15000, 3),
    ("DEMO-ORDER-004", "demo-customer-001", 5000, 2),
    ("DEMO-ORDER-005", "demo-customer-004", 10000, 1),
)


def _product(db, *, store_id: str, name: str, price: int) -> Product:
    product = db.scalar(
        select(Product).where(Product.store_id == store_id, Product.name == name)
    )
    if product is None:
        product = Product(store_id=store_id, name=name, price=price)
        db.add(product)
        db.flush()
    return product


def _ensure_order(
    db,
    *,
    store: Store,
    product: Product,
    external_order_id: str,
    customer_key: str,
    total_amount: int,
    hours_ago: int,
    now,
    business_day,
) -> Order:
    order = db.scalar(
        select(Order).where(
            Order.store_id == store.id,
            Order.external_order_id == external_order_id,
        )
    )
    if order is not None:
        return order

    paid_at = now - timedelta(hours=hours_ago)
    order = Order(
        store_id=store.id,
        external_order_id=external_order_id,
        customer_key=customer_key,
        status="PAID",
        total_amount=total_amount,
        refunded_amount=0,
        wifi_minutes=120,
        business_date=business_day,
        paid_at=paid_at,
    )
    db.add(order)
    db.flush()
    db.add(
        OrderItem(
            order_id=order.id,
            product_id=product.id,
            name_snapshot=product.name,
            quantity=max(1, total_amount // product.price),
            unit_price=product.price,
        )
    )
    return order


def _ensure_pass(
    db,
    *,
    store: Store,
    customer_key: str,
    business_day,
    now,
    index: int,
) -> WiFiPass:
    wifi_pass = db.scalar(
        select(WiFiPass).where(
            WiFiPass.store_id == store.id,
            WiFiPass.customer_key == customer_key,
            WiFiPass.business_date == business_day,
        )
    )
    if wifi_pass is None:
        wifi_pass = WiFiPass(
            store_id=store.id,
            customer_key=customer_key,
            business_date=business_day,
            status="ACTIVE",
            issued_at=now - timedelta(minutes=120),
            activated_at=now - timedelta(minutes=45),
            expires_at=now + timedelta(minutes=75 + index * 15),
            version=1,
            policy_snapshot={"source": "DEMO_SEED", "minutes": 120},
            network_reference=f"demo-network-{index:03d}",
        )
        db.add(wifi_pass)
    return wifi_pass


def _ensure_inventory(
    db,
    *,
    store: Store,
    product: Product,
    quantity: int,
    low_stock_threshold: int,
    expires_on,
    today,
) -> InventoryItem:
    item = db.scalar(
        select(InventoryItem).where(
            InventoryItem.store_id == store.id,
            InventoryItem.product_id == product.id,
        )
    )
    created = item is None
    if item is None:
        item = InventoryItem(store_id=store.id, product_id=product.id)
        db.add(item)
        db.flush()
    item.quantity = quantity
    item.low_stock_threshold = low_stock_threshold
    item.expires_on = expires_on
    item.unit = "EA"
    item.risk_score = calculate_risk_score(item, today=today)
    item.updated_at = db_now()
    if created:
        db.add(
            InventoryEvent(
                item_id=item.id,
                type="SEEDED",
                quantity_delta=quantity,
                reason="MVP 시연용 재고 시드",
            )
        )
    return item


def seed_demo_data(
    *,
    store_name: str = "펭귄 카페 MVP",
    username: str = "demo-owner",
    password: str = "demo-password",
) -> dict:
    base = seed(store_name=store_name, username=username, password=password)
    now = db_now()
    with session_scope() as db:
        store = db.get(Store, base["storeId"])
        business_day = business_date(
            now,
            timezone_name=store.timezone,
            cutoff=store.business_day_cutoff,
        )
        products = [
            _product(db, store_id=store.id, name="아메리카노", price=5000),
            _product(db, store_id=store.id, name="카페라떼", price=6000),
            _product(db, store_id=store.id, name="딸기케이크", price=7000),
        ]
        primary_product = products[0]
        orders = [
            _ensure_order(
                db,
                store=store,
                product=primary_product,
                external_order_id=external_order_id,
                customer_key=customer_key,
                total_amount=total_amount,
                hours_ago=hours_ago,
                now=now,
                business_day=business_day,
            )
            for external_order_id, customer_key, total_amount, hours_ago in DEMO_ORDERS
        ]
        demo_customer_keys = list(dict.fromkeys(item[1] for item in DEMO_ORDERS[:4]))
        passes = [
            _ensure_pass(
                db,
                store=store,
                customer_key=customer_key,
                business_day=business_day,
                now=now,
                index=index,
            )
            for index, customer_key in enumerate(demo_customer_keys, start=1)
        ]
        inventory = [
            _ensure_inventory(
                db,
                store=store,
                product=products[0],
                quantity=48,
                low_stock_threshold=10,
                expires_on=None,
                today=business_day,
            ),
            _ensure_inventory(
                db,
                store=store,
                product=products[1],
                quantity=3,
                low_stock_threshold=5,
                expires_on=business_day + timedelta(days=2),
                today=business_day,
            ),
            _ensure_inventory(
                db,
                store=store,
                product=products[2],
                quantity=0,
                low_stock_threshold=2,
                expires_on=business_day - timedelta(days=1),
                today=business_day,
            ),
        ]
        summary = sales_summary(db, store=store, business_date=business_day)
        db.flush()
        return {
            **base,
            "businessDate": business_day.isoformat(),
            "orderCount": len(orders),
            "activePassCount": sum(item.status in {"ACTIVE", "EXPIRING_SOON"} for item in passes),
            "inventoryCount": len(inventory),
            "totalSales": summary["totalSales"],
            "totalOrders": summary["totalOrders"],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart WiFi Pass MVP demo data seed")
    parser.add_argument("--store-name", default="펭귄 카페 MVP")
    parser.add_argument("--username", default="demo-owner")
    parser.add_argument("--password", default="demo-password")
    args = parser.parse_args()

    init_db()
    result = seed_demo_data(
        store_name=args.store_name,
        username=args.username,
        password=args.password,
    )
    print("MVP demo data seed complete")
    for key, value in result.items():
        print(f"{key}: {value}")
    print(f"password: {args.password}")


if __name__ == "__main__":
    main()
