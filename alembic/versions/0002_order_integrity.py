"""add order integrity and idempotency fields

Revision ID: 0002_order_integrity
Revises: 0001_initial
"""

from alembic import op
from sqlalchemy import Column, DateTime, Integer, JSON, String, inspect


revision = "0002_order_integrity"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _add_columns(table: str, columns: list[Column]) -> None:
    inspector = inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns(table)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)


def upgrade() -> None:
    _add_columns(
        "orders",
        [
            Column("status", String(20), nullable=False, server_default="PAID"),
            Column("phone_lookup_hash", String(64), nullable=True),
            Column("phone_last4", String(4), nullable=True),
            Column("refunded_amount", Integer, nullable=False, server_default="0"),
            Column("wifi_minutes", Integer, nullable=False, server_default="0"),
        ],
    )
    _add_columns(
        "otp_challenges",
        [Column("phone_lookup_hash", String(64), nullable=True)],
    )
    _add_columns(
        "daily_spend_balances",
        [Column("version", Integer, nullable=False, server_default="1")],
    )
    _add_columns(
        "reward_grants",
        [Column("fulfilled_at", DateTime(timezone=True), nullable=True)],
    )
    _add_columns(
        "coupons",
        [Column("created_at", DateTime(timezone=True), nullable=True)],
    )
    _add_columns(
        "admin_users",
        [Column("role", String(20), nullable=False, server_default="OWNER")],
    )

    inspector = inspect(op.get_bind())
    if "idempotency_records" not in inspector.get_table_names():
        op.create_table(
            "idempotency_records",
            Column("id", String(36), primary_key=True),
            Column("scope", String(180), nullable=False),
            Column("key", String(200), nullable=False),
            Column("request_hash", String(64), nullable=False),
            Column("status_code", Integer, nullable=False, server_default="200"),
            Column("response_json", JSON, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_idempotency_records_scope", "idempotency_records", ["scope"])
        op.create_unique_constraint(
            "uq_idempotency_scope_key", "idempotency_records", ["scope", "key"]
        )


def downgrade() -> None:
    op.drop_table("idempotency_records")
    for table, columns in {
        "admin_users": ["role"],
        "coupons": ["created_at"],
        "reward_grants": ["fulfilled_at"],
        "daily_spend_balances": ["version"],
        "otp_challenges": ["phone_lookup_hash"],
        "orders": ["wifi_minutes", "refunded_amount", "phone_last4", "phone_lookup_hash", "status"],
    }.items():
        for column in columns:
            op.drop_column(table, column)
