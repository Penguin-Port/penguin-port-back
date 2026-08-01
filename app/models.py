from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.time import db_now


def uuid_value() -> str:
    return str(uuid4())


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    name: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Seoul")
    business_day_cutoff: Mapped[str] = mapped_column(String(5), default="00:00")
    otp_skip_enabled: Mapped[bool] = mapped_column(default=False)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("store_id", "name", name="uq_product_store_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    price: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(default=True)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("store_id", "external_order_id", name="uq_order_store_external"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    external_order_id: Mapped[str] = mapped_column(String(120))
    customer_key: Mapped[str] = mapped_column(String(160), index=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    total_amount: Mapped[int] = mapped_column(Integer)
    business_date: Mapped[date] = mapped_column(Date, index=True)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=db_now)


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    name_snapshot: Mapped[str] = mapped_column(String(120))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[int] = mapped_column(Integer)


class OrderClaim(Base):
    __tablename__ = "order_claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exchanged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=db_now)


class OtpChallenge(Base):
    __tablename__ = "otp_challenges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    customer_key: Mapped[str] = mapped_column(String(160), index=True)
    phone: Mapped[str] = mapped_column(String(40))
    code_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=db_now)


class DemoMessage(Base):
    __tablename__ = "demo_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="SMS")
    destination: Mapped[str] = mapped_column(String(40))
    body: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=db_now)


class WiFiPass(Base):
    __tablename__ = "wifi_passes"
    __table_args__ = (
        UniqueConstraint("store_id", "customer_key", "business_date", name="uq_pass_daily_customer"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    customer_key: Mapped[str] = mapped_column(String(160), index=True)
    business_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(24), default="ISSUED", index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    network_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)


class DailySpendBalance(Base):
    __tablename__ = "daily_spend_balances"
    __table_args__ = (
        UniqueConstraint("store_id", "business_date", "customer_key", name="uq_daily_spend"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    business_date: Mapped[date] = mapped_column(Date)
    customer_key: Mapped[str] = mapped_column(String(160))
    total_amount: Mapped[int] = mapped_column(Integer, default=0)


class RewardTier(Base):
    __tablename__ = "reward_tiers"
    __table_args__ = (
        UniqueConstraint("store_id", "threshold_amount", name="uq_reward_tier_threshold"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    threshold_amount: Mapped[int] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class RewardBenefit(Base):
    __tablename__ = "reward_benefits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    tier_id: Mapped[str] = mapped_column(ForeignKey("reward_tiers.id"), index=True)
    benefit_type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RewardGrant(Base):
    __tablename__ = "reward_grants"
    __table_args__ = (
        UniqueConstraint("store_id", "business_date", "customer_key", "tier_id", name="uq_reward_grant"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    business_date: Mapped[date] = mapped_column(Date)
    customer_key: Mapped[str] = mapped_column(String(160))
    tier_id: Mapped[str] = mapped_column(ForeignKey("reward_tiers.id"))
    status: Mapped[str] = mapped_column(String(24), default="AWAITING_CHOICE")
    chosen_benefit_id: Mapped[str | None] = mapped_column(ForeignKey("reward_benefits.id"), nullable=True)
    fulfill_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=db_now)


class Coupon(Base):
    __tablename__ = "coupons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    customer_key: Mapped[str] = mapped_column(String(160), index=True)
    grant_id: Mapped[str] = mapped_column(ForeignKey("reward_grants.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="AVAILABLE")
    benefit_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AIRecommendation(Base):
    __tablename__ = "ai_recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    type: Mapped[str] = mapped_column(String(40), default="TIME_SALE")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=db_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Promotion(Base):
    __tablename__ = "promotions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("ai_recommendations.id"), unique=True)
    title: Mapped[str] = mapped_column(String(160))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="SCHEDULED")


class AdminUser(Base):
    __tablename__ = "admin_users"
    __table_args__ = (UniqueConstraint("username", name="uq_admin_username"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    username: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(256))
