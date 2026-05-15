from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


EventType = Literal[
    "product_view",
    "page_view",
    "add_to_cart",
    "session_start",
    "session_end",
]
DeviceType = Literal["mobile", "desktop"]


class EventCreate(BaseModel):
    store_id: int | None = None
    session_id: str = Field(min_length=1, max_length=255)
    event_type: EventType
    product_id: int | str | None = None
    page_url: str | None = None
    referrer: str | None = None
    device_type: DeviceType | None = None
    time_spent: int | None = Field(default=None, ge=0)
    timestamp: datetime | None = None
    customer_id: int | str | None = None
    is_first_time: bool | None = None
    time_since_last_visit: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EventAck(BaseModel):
    status: str


class EventSummary(BaseModel):
    total_events: int
    product_views: int
    sessions: int
    avg_time_spent: float


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: str
    visit_count: int
    last_seen_at: datetime
