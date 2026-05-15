from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.intent import IntentLevel

MessageStatus = Literal["draft", "approved", "rejected", "sent"]
MessageChannel = Literal["email", "whatsapp", "sms"]


class CustomerMessage(BaseModel):
    customer_id: int
    customer_name: str
    intent: IntentLevel
    score: int
    product_id: int | None
    product_title: str | None
    recommendation_reason: str | None
    message: str


class GenerateMessagesRequest(BaseModel):
    limit: int = Field(default=25, ge=1, le=100)
    channel: MessageChannel = "email"


class RegenerateMessageRequest(BaseModel):
    channel: MessageChannel | None = None


class GeneratedMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    customer_id: int
    customer_name: str
    product_id: int | None
    channel: str
    intent: str
    score: int
    product_title: str | None
    recommendation_reason: str | None
    message: str
    status: str
    provider: str
    model_name: str | None
    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None
    rejected_at: datetime | None


class ApproveMessageResponse(GeneratedMessageResponse):
    pass


class RejectMessageResponse(GeneratedMessageResponse):
    pass
