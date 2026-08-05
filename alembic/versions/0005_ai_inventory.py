"""add analytics and inventory MVP tables

Revision ID: 0005_ai_inventory
Revises: 0004_admin_operations
"""

from alembic import op
from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, JSON, String, inspect


revision = "0005_ai_inventory"
down_revision = "0004_admin_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    ai_columns = {column["name"] for column in inspector.get_columns("ai_recommendations")}
    if "evidence" not in ai_columns:
        op.add_column("ai_recommendations", Column("evidence", JSON, nullable=True))
    if "confidence" not in ai_columns:
        op.add_column("ai_recommendations", Column("confidence", Float, nullable=True))
    tables = inspector.get_table_names()
    if "analytics_hourly" not in tables:
        op.create_table(
            "analytics_hourly",
            Column("id", String(36), primary_key=True),
            Column("store_id", String(36), ForeignKey("stores.id"), nullable=False),
            Column("bucket_start", DateTime(timezone=True), nullable=False),
            Column("order_count", Integer, nullable=False, server_default="0"),
            Column("gross_sales", Integer, nullable=False, server_default="0"),
            Column("wifi_active_count", Integer, nullable=False, server_default="0"),
            Column("wifi_active_minutes", Integer, nullable=False, server_default="0"),
            Column("menu_sales", JSON, nullable=False),
            Column("repeat_customer_count", Integer, nullable=False, server_default="0"),
            Column("generated_at", DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_analytics_hourly_store_id", "analytics_hourly", ["store_id"])
        op.create_index("ix_analytics_hourly_bucket_start", "analytics_hourly", ["bucket_start"])
        op.create_unique_constraint(
            "uq_analytics_store_bucket", "analytics_hourly", ["store_id", "bucket_start"]
        )
    if "inventory_items" not in tables:
        op.create_table(
            "inventory_items",
            Column("id", String(36), primary_key=True),
            Column("store_id", String(36), ForeignKey("stores.id"), nullable=False),
            Column("product_id", String(36), ForeignKey("products.id"), nullable=False),
            Column("quantity", Integer, nullable=False, server_default="0"),
            Column("unit", String(20), nullable=False, server_default="EA"),
            Column("low_stock_threshold", Integer, nullable=False, server_default="0"),
            Column("expires_on", Date, nullable=True),
            Column("risk_score", Integer, nullable=False, server_default="0"),
            Column("updated_at", DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_inventory_items_store_id", "inventory_items", ["store_id"])
        op.create_index("ix_inventory_items_product_id", "inventory_items", ["product_id"])
        op.create_index("ix_inventory_items_risk_score", "inventory_items", ["risk_score"])
        op.create_unique_constraint(
            "uq_inventory_store_product", "inventory_items", ["store_id", "product_id"]
        )
    if "inventory_events" not in tables:
        op.create_table(
            "inventory_events",
            Column("id", String(36), primary_key=True),
            Column("item_id", String(36), ForeignKey("inventory_items.id"), nullable=False),
            Column("type", String(24), nullable=False),
            Column("quantity_delta", Integer, nullable=False, server_default="0"),
            Column("reason", String(240), nullable=False, server_default=""),
            Column("occurred_at", DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_inventory_events_item_id", "inventory_events", ["item_id"])


def downgrade() -> None:
    op.drop_table("inventory_events")
    op.drop_table("inventory_items")
    op.drop_table("analytics_hourly")
    op.drop_column("ai_recommendations", "confidence")
    op.drop_column("ai_recommendations", "evidence")
