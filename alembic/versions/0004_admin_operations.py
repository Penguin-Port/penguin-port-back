"""add admin sessions and configurable Wi-Fi policy

Revision ID: 0004_admin_operations
Revises: 0003_reward_redemptions
"""

from alembic import op
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, inspect


revision = "0004_admin_operations"
down_revision = "0003_reward_redemptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    store_columns = {column["name"] for column in inspector.get_columns("stores")}
    if "policy_config" not in store_columns:
        op.add_column("stores", Column("policy_config", JSON, nullable=True, server_default="{}"))
    if "policy_version" not in store_columns:
        op.add_column("stores", Column("policy_version", Integer, nullable=False, server_default="1"))

    if "refresh_token_sessions" not in inspector.get_table_names():
        op.create_table(
            "refresh_token_sessions",
            Column("id", String(36), primary_key=True),
            Column("admin_id", String(36), ForeignKey("admin_users.id"), nullable=False),
            Column("token_hash", String(64), nullable=False),
            Column("expires_at", DateTime(timezone=True), nullable=False),
            Column("revoked_at", DateTime(timezone=True), nullable=True),
            Column("replaced_by", String(36), nullable=True),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_refresh_token_sessions_admin_id", "refresh_token_sessions", ["admin_id"]
        )
        op.create_index(
            "ix_refresh_token_sessions_token_hash", "refresh_token_sessions", ["token_hash"]
        )
        op.create_unique_constraint(
            "uq_refresh_token_sessions_token_hash", "refresh_token_sessions", ["token_hash"]
        )


def downgrade() -> None:
    op.drop_table("refresh_token_sessions")
    op.drop_column("stores", "policy_version")
    op.drop_column("stores", "policy_config")
