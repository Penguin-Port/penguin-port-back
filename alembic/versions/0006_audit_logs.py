"""add audit log table

Revision ID: 0006_audit_logs
Revises: 0005_ai_inventory
"""

from alembic import op
from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, inspect


revision = "0006_audit_logs"
down_revision = "0005_ai_inventory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "audit_logs" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "audit_logs",
        Column("id", String(36), primary_key=True),
        Column("store_id", String(36), ForeignKey("stores.id"), nullable=False),
        Column("actor_type", String(24), nullable=False),
        Column("actor_id", String(36), nullable=True),
        Column("action", String(80), nullable=False),
        Column("resource_type", String(40), nullable=False),
        Column("resource_id", String(36), nullable=True),
        Column("metadata", JSON, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_store_id", "audit_logs", ["store_id"])


def downgrade() -> None:
    op.drop_table("audit_logs")
