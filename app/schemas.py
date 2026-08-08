from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OrderItemRequest(BaseModel):
    productId: str
    quantity: int = Field(ge=1)
    unitPrice: int | None = Field(default=None, ge=0)


class CustomerRequest(BaseModel):
    memberId: str | None = None
    phone: str | None = None


class PosOrderRequest(BaseModel):
    storeId: str
    externalOrderId: str
    customer: CustomerRequest
    items: list[OrderItemRequest] = Field(min_length=1)
    totalAmount: int = Field(ge=0)
    paidAt: datetime


class PosRefundRequest(BaseModel):
    storeId: str
    refundAmount: int | None = Field(default=None, ge=1)
    reason: str = ""


class ClaimExchangeRequest(BaseModel):
    orderClaim: str


class OtpSendRequest(BaseModel):
    verificationTicket: str
    phone: str


class OtpConfirmRequest(BaseModel):
    challengeId: str
    code: str = Field(pattern=r"^\d{6}$")


class RewardChooseRequest(BaseModel):
    benefitId: str
    fulfillMode: str = Field(pattern=r"^(IMMEDIATE|COUPON_7D)$")
    orderId: str | None = None


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminRefreshRequest(BaseModel):
    refreshToken: str | None = None


class AdminLogoutRequest(BaseModel):
    refreshToken: str | None = None


class AdminTeamMemberCreateRequest(BaseModel):
    storeId: str
    username: str = Field(min_length=3, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="STAFF", pattern=r"^(OWNER|MANAGER|STAFF|VIEWER)$")


class AdminTeamMemberPatchRequest(BaseModel):
    storeId: str
    role: str | None = Field(default=None, pattern=r"^(OWNER|MANAGER|STAFF|VIEWER)$")
    isActive: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)


class AdminPassExtendRequest(BaseModel):
    storeId: str | None = None
    minutes: int = Field(ge=1)


class AdminPassExpireRequest(BaseModel):
    storeId: str | None = None


class AdminPassBlockRequest(BaseModel):
    storeId: str | None = None
    reason: str = Field(default="", max_length=240)


class RecommendationDecisionRequest(BaseModel):
    storeId: str
    version: int = Field(ge=1)
    menuIds: list[str] | None = None
    discountRate: int | None = Field(default=None, ge=0, le=100)
    startsAt: datetime | None = None
    endsAt: datetime | None = None


class RecommendationPatchRequest(RecommendationDecisionRequest):
    pass


class RecommendationRejectRequest(BaseModel):
    storeId: str | None = None
    reason: str = ""


class RecommendationGenerateRequest(BaseModel):
    storeId: str
    type: str = Field(
        default="TIME_SALE",
        pattern=r"^(TIME_SALE|SALES_SUMMARY|INVENTORY_PROMOTION|MENU_TREND)$",
    )
    businessDate: date | None = None


class PolicyTierRequest(BaseModel):
    minAmount: int = Field(ge=0)
    minutes: int = Field(ge=0)


class WifiPolicyPublishRequest(BaseModel):
    version: int = Field(ge=1)
    baseMinutes: int = Field(ge=1, le=1440)
    firstOrderTiers: list[PolicyTierRequest] = Field(default_factory=list, max_length=8)
    additionalOrderTiers: list[PolicyTierRequest] = Field(default_factory=list, max_length=8)


class WifiPolicySimulationRequest(BaseModel):
    orderType: str = Field(pattern=r"^(FIRST|ADDITIONAL)$")
    amount: int = Field(ge=0)


class InventoryUpsertRequest(BaseModel):
    storeId: str
    productId: str
    quantity: int = Field(ge=0)
    lowStockThreshold: int = Field(default=0, ge=0)
    expiresOn: date | None = None
    unit: str = "EA"


class InventoryAdjustRequest(BaseModel):
    storeId: str
    quantityDelta: int
    reason: str = ""


class RewardBenefitUpsertRequest(BaseModel):
    benefitId: str | None = None
    benefitType: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)


class RewardTierUpsertRequest(BaseModel):
    storeId: str
    tierId: str | None = None
    name: str = Field(min_length=1, max_length=80)
    thresholdAmount: int = Field(ge=0)
    sortOrder: int = Field(ge=0)
    benefits: list[RewardBenefitUpsertRequest] = Field(default_factory=list, max_length=12)


class SuccessEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: Any
    meta: dict[str, Any]
