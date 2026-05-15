from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SyncRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    products_synced: int
    customers_synced: int
    orders_synced: int
    error_message: str | None


class SyncResult(BaseModel):
    status: str
    products_synced: int
    customers_synced: int
    orders_synced: int
