from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


IntentLevel = Literal["high", "medium", "low"]


class IntentSignals(BaseModel):
    product_views: int
    sessions: int
    time_spent: int
    added_to_cart: int
    days_since_last_visit: int | None
    days_since_last_order: int | None


class CustomerIntent(BaseModel):
    customer_id: int
    name: str
    email: str | None
    intent: IntentLevel
    score: int
    signals: IntentSignals
    reason: str
    last_visit_at: datetime | None
    last_order_date: datetime | None
