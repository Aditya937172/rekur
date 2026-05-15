from __future__ import annotations

from pydantic import BaseModel, Field


class GmailAuthUrlResponse(BaseModel):
    auth_url: str
    redirect_uri: str
    scope: str
    sender_email: str | None
    refresh_token_configured: bool


class GmailTokenExchangeRequest(BaseModel):
    code: str = Field(min_length=1)


class GmailTokenExchangeResponse(BaseModel):
    refresh_token: str | None
    refresh_token_present: bool
    expires_in: int | None = None
    token_type: str | None = None
    instruction: str


class SendApprovedMessageRequest(BaseModel):
    recipient_email: str | None = Field(default=None, max_length=320)
    subject: str | None = Field(default=None, max_length=200)


class SendApprovedMessageResponse(BaseModel):
    message_id: int
    status: str
    provider: str
    provider_message_id: str | None
    sender_email: str
    recipient_email: str
    customer_id: int
    subject: str
