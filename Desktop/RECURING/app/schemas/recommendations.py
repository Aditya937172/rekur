from __future__ import annotations

from pydantic import BaseModel

from app.schemas.intent import IntentLevel


class ProductRecommendation(BaseModel):
    product_id: int
    shopify_product_id: str
    title: str
    reason: str
    image_url: str | None = None
    price: float | None = None


class CustomerRecommendations(BaseModel):
    customer_id: int
    intent: IntentLevel
    score: int
    recommendations: list[ProductRecommendation]
