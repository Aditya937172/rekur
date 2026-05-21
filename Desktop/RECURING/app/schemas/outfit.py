from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GenerateOutfitImageRequest(BaseModel):
    customer_id: int
    order_id: int | None = None
    trigger_reason: str = "order_delivered_followup"
    send_email: bool = False
    recipient_email: str | None = Field(default=None, max_length=320)


class FirstOrderAnniversaryCampaignRequest(BaseModel):
    customer_id: int | None = None
    days_window: int = Field(default=7, ge=1, le=365)
    limit: int = Field(default=10, ge=1, le=100)
    send_email: bool = False
    recipient_email: str | None = Field(default=None, max_length=320)
    force: bool = False


class SendOutfitEmailRequest(BaseModel):
    recipient_email: str | None = Field(default=None, max_length=320)
    subject: str | None = Field(default=None, max_length=255)


class GeneratedOutfitImageResponse(BaseModel):
    id: int
    store_id: int
    customer_id: int
    order_id: int | None
    buyer_memory_id: int | None
    trigger_reason: str
    status: str
    provider: str
    model_name: str | None
    task_id: str | None
    task_status: str | None
    task_progress: int | None
    credits_reserved: float | None = None
    credits_used: float | None = None
    image_input_tokens: int | None = None
    image_output_tokens: int | None = None
    text_input_tokens: int | None = None
    total_tokens: int | None = None
    image_generation_usage_json: dict[str, Any] | None = None
    prompt: str
    image_url: str | None
    recommended_products_json: list[dict[str, Any]]
    reference_image_urls_json: list[str]
    email_subject: str | None
    email_body: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None


class OutfitEmailSendResponse(BaseModel):
    outfit_id: int
    status: str
    provider_message_id: str | None
    recipient_email: str
    subject: str


class AnniversarySkippedCustomer(BaseModel):
    customer_id: int
    reason: str


class FirstOrderAnniversaryCampaignResponse(BaseModel):
    store_id: int
    trigger_reason: str
    processed: int
    generated: int
    sent: int
    skipped: list[AnniversarySkippedCustomer]
    outfits: list[GeneratedOutfitImageResponse]
