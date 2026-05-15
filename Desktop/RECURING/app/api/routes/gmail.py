from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.core.config import load_settings
from app.schemas import (
    GmailAuthUrlResponse,
    GmailTokenExchangeRequest,
    GmailTokenExchangeResponse,
)
from app.services.gmail_service import (
    GmailServiceError,
    build_gmail_auth_url,
    exchange_code_for_tokens,
)


router = APIRouter(prefix="/gmail", tags=["gmail"])


@router.get("/auth-url", response_model=GmailAuthUrlResponse)
async def get_gmail_auth_url(
    state: str | None = Query(default=None),
) -> GmailAuthUrlResponse:
    settings = load_settings()
    try:
        auth_url = build_gmail_auth_url(settings=settings, state=state)
    except GmailServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    return GmailAuthUrlResponse(
        auth_url=auth_url,
        redirect_uri=settings.gmail_redirect_uri,
        scope=settings.gmail_send_scope,
        sender_email=settings.gmail_sender_email,
        refresh_token_configured=bool(settings.gmail_refresh_token),
    )


@router.post("/exchange-code", response_model=GmailTokenExchangeResponse)
async def exchange_gmail_oauth_code(
    request: GmailTokenExchangeRequest,
) -> GmailTokenExchangeResponse:
    try:
        tokens = exchange_code_for_tokens(request.code)
    except GmailServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    refresh_token = tokens.get("refresh_token")
    return GmailTokenExchangeResponse(
        refresh_token=refresh_token,
        refresh_token_present=bool(refresh_token),
        expires_in=tokens.get("expires_in"),
        token_type=tokens.get("token_type"),
        instruction=(
            "Add refresh_token to .env as GMAIL_REFRESH_TOKEN. "
            "Google returns it only on consent flows with access_type=offline and prompt=consent."
        ),
    )


@router.get("/oauth/callback", response_model=GmailTokenExchangeResponse)
async def gmail_oauth_callback(
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> GmailTokenExchangeResponse:
    if error:
        raise HTTPException(status_code=400, detail=f"Google OAuth failed: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Google OAuth code is missing.")

    try:
        tokens = exchange_code_for_tokens(code)
    except GmailServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    refresh_token = tokens.get("refresh_token")
    return GmailTokenExchangeResponse(
        refresh_token=refresh_token,
        refresh_token_present=bool(refresh_token),
        expires_in=tokens.get("expires_in"),
        token_type=tokens.get("token_type"),
        instruction=(
            "Copy refresh_token into .env as GMAIL_REFRESH_TOKEN, then restart the API. "
            "Do not commit or share this token."
        ),
    )
