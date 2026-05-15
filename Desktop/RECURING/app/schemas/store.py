from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class StoreCreate(BaseModel):
    name: str
    nango_connection_id: str
    shopify_store_domain: str


class StoreRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    nango_connection_id: str
    shopify_store_domain: str
    created_at: datetime
    tracking_installed: bool
    tracking_installed_at: datetime | None


class StoreDashboard(BaseModel):
    products: int
    customers: int
    orders: int
    last_sync: datetime | None


class TrackingInstallResult(BaseModel):
    status: str
    store_id: int
    tracking_installed: bool
    tracking_installed_at: datetime | None
    script_url: str
    message: str
