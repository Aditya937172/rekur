from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class BuyerMemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    customer_id: int
    first_order_at: datetime | None
    last_order_at: datetime | None
    last_order_id: int | None
    total_orders: int
    total_spent: Decimal
    first_order_summary: str | None
    last_order_summary: str | None
    wardrobe_summary: str | None
    interest_summary: str | None
    memory_summary: str | None
    favorite_categories: str | None
    favorite_colors: str | None
    style_tags: str | None
    price_band: str | None
    order_history_json: list[dict[str, Any]]
    wardrobe_items_json: list[dict[str, Any]]
    recent_interests_json: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime


class BuyerMemoryRebuildResponse(BaseModel):
    store_id: int
    memories_updated: int
