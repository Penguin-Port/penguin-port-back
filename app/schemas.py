from datetime import datetime
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


class AdminPassExtendRequest(BaseModel):
    storeId: str | None = None
    minutes: int = Field(ge=1)


class AdminPassExpireRequest(BaseModel):
    storeId: str | None = None


class RecommendationDecisionRequest(BaseModel):
    storeId: str
    version: int = Field(ge=1)


class RecommendationRejectRequest(BaseModel):
    storeId: str | None = None
    reason: str = ""


class SuccessEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: Any
    meta: dict[str, Any]
