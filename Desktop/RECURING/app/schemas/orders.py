from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.outfit import GeneratedOutfitImageResponse, OutfitEmailSendResponse


class DeliveredOrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(default=1, ge=1)
    price: Decimal | None = Field(default=None, ge=0)


class DeliveredOrderCreateRequest(BaseModel):
    customer_id: int
    items: list[DeliveredOrderItemCreate] = Field(min_length=1)
    shopify_order_id: str | None = Field(default=None, max_length=64)
    currency: str = Field(default="INR", max_length=16)
    delivered_at: datetime | None = None
    send_email: bool = False
    recipient_email: str | None = Field(default=None, max_length=320)


class DeliveredOrderPipelineResponse(BaseModel):
    order_id: int
    shopify_order_id: str
    customer_id: int
    fulfillment_status: str
    delivered_at: datetime
    outfit: GeneratedOutfitImageResponse
    email: OutfitEmailSendResponse | None = None
