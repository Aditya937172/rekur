from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.outfit import GeneratedOutfitImageResponse


class EmailEngagementCreate(BaseModel):
    customer_id: int | None = None
    send_log_id: int | None = None
    provider_message_id: str | None = Field(default=None, max_length=255)
    campaign_type: str | None = Field(default=None, max_length=128)
    event_type: str = Field(max_length=64)
    url: str | None = None
    timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmailEngagementResponse(BaseModel):
    id: int
    store_id: int
    customer_id: int | None
    event_type: str
    campaign_type: str | None
    timestamp: datetime


class ReturnRefundCreate(BaseModel):
    customer_id: int | None = None
    order_id: int | None = None
    shopify_refund_id: str | None = Field(default=None, max_length=64)
    status: str = Field(default="recorded", max_length=64)
    amount: Decimal = Field(default=Decimal("0"), ge=0)
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReturnRefundResponse(BaseModel):
    id: int
    store_id: int
    customer_id: int | None
    order_id: int | None
    status: str
    amount: Decimal
    created_at: datetime


class CampaignRunRequest(BaseModel):
    customer_id: int | None = None
    limit: int = Field(default=25, ge=1, le=500)
    send_email: bool = False
    recipient_email: str | None = Field(default=None, max_length=320)
    force: bool = False


class CampaignRunResponse(BaseModel):
    store_id: int
    campaign_type: str
    processed: int
    generated: int = 0
    sent: int = 0
    skipped: list[dict[str, Any]] = Field(default_factory=list)
    outfits: list[GeneratedOutfitImageResponse] = Field(default_factory=list)


class ChurnRiskResponse(BaseModel):
    customer_id: int
    customer_name: str
    score: float
    stage: str
    signals: dict[str, Any]


class SilentCustomerResponse(BaseModel):
    customer_id: int
    customer_name: str
    last_purchase_days: int | None
    open_rate_60d: float
    click_rate_60d: float
    emails_sent_60d: int


class CustomerReplyCreate(BaseModel):
    customer_id: int
    send_log_id: int | None = None
    inbound_text: str = Field(min_length=1)


class CustomerReplyResponse(BaseModel):
    id: int
    customer_id: int
    extracted_preferences: dict[str, Any]
    response_text: str
    created_at: datetime
