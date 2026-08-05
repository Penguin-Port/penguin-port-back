"""add active state and creation time to FastAPI admin users

Revision ID: 0008_admin_team_fields
Revises: 0007_backend_events
"""

from alembic import op
from sqlalchemy import Boolean, Column, DateTime, inspect, text


revision = "0008_admin_team_fields"
down_revision = "0007_backend_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("admin_users")}
    if "is_active" not in columns:
        op.add_column(
            "admin_users",
            Column("is_active", Boolean(), nullable=False, server_default=text("1")),
        )
    if "created_at" not in columns:
        op.add_column(
            "admin_users",
            Column("created_at", DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("admin_users")}
    if "created_at" in columns:
        op.drop_column("admin_users", "created_at")
    if "is_active" in columns:
        op.drop_column("admin_users", "is_active")

