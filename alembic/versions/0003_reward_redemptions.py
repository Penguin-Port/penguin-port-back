"""add immediate reward redemption records

Revision ID: 0003_reward_redemptions
Revises: 0002_order_integrity
"""

from alembic import op
from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, JSON, String, inspect


revision = "0003_reward_redemptions"
down_revision = "0002_order_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "reward_redemptions" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "reward_redemptions",
        Column("id", String(36), primary_key=True),
        Column("store_id", String(36), ForeignKey("stores.id"), nullable=False),
        Column("grant_id", String(36), ForeignKey("reward_grants.id"), nullable=False),
        Column("benefit_id", String(36), ForeignKey("reward_benefits.id"), nullable=False),
        Column("customer_key", String(160), nullable=False),
        Column("business_date", Date, nullable=False),
        Column("status", String(20), nullable=False, server_default="AVAILABLE"),
        Column("order_id", String(36), ForeignKey("orders.id"), nullable=True),
        Column("benefit_snapshot", JSON, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("consumed_at", DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_reward_redemptions_store_id", "reward_redemptions", ["store_id"])
    op.create_index(
        "ix_reward_redemptions_customer_key", "reward_redemptions", ["customer_key"]
    )
    op.create_index(
        "ix_reward_redemptions_business_date", "reward_redemptions", ["business_date"]
    )
    op.create_index("ix_reward_redemptions_status", "reward_redemptions", ["status"])
    op.create_unique_constraint("uq_reward_redemption_grant", "reward_redemptions", ["grant_id"])


def downgrade() -> None:
    op.drop_table("reward_redemptions")
