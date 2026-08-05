"""add durable FastAPI event stream rows

Revision ID: 0007_backend_events
Revises: 0006_audit_logs
"""

from alembic import op
from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, inspect


revision = "0007_backend_events"
down_revision = "0006_audit_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "backend_events" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "backend_events",
        Column("id", String(36), primary_key=True),
        Column("store_id", String(36), ForeignKey("stores.id"), nullable=False),
        Column("event_type", String(100), nullable=False),
        Column("aggregate_type", String(80), nullable=False),
        Column("aggregate_id", String(80), nullable=False),
        Column("payload", JSON, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_backend_events_store_id", "backend_events", ["store_id"])
    op.create_index(
        "ix_backend_events_store_created",
        "backend_events",
        ["store_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_backend_events_store_created", table_name="backend_events")
    op.drop_index("ix_backend_events_store_id", table_name="backend_events")
    op.drop_table("backend_events")

