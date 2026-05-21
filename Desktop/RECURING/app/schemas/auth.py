from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.store import StoreRead


class AppUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str | None
    auth_provider: str
    created_at: datetime


class DevTokenRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str | None = Field(default=None, max_length=255)


class DevTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AppUserRead


class ShopifyConnectStartRequest(BaseModel):
    success_url: str | None = None
    error_url: str | None = None


class ShopifyConnectStartResponse(BaseModel):
    provider_config_key: str
    connect_url: str | None = None
    connect_session_token: str | None = None
    raw_session: dict


class ShopifyConnectCallbackRequest(BaseModel):
    connection_id: str = Field(min_length=1, max_length=255)
    shopify_store_domain: str | None = Field(default=None, max_length=255)
    name: str | None = Field(default=None, max_length=255)
    provider_config_key: str | None = Field(default=None, max_length=255)
    auth_state: str | None = None


class ShopifyConnectCallbackResponse(BaseModel):
    status: str
    created: bool
    store: StoreRead
    next_sync_url: str
