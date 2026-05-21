from __future__ import annotations

from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import (
    create_access_token,
    decode_token,
    get_current_user,
    get_or_create_app_user,
)
from app.core.config import load_settings
from app.db.session import get_db
from app.models import AppUser
from app.schemas import (
    AppUserRead,
    DevTokenRequest,
    DevTokenResponse,
    ShopifyConnectCallbackRequest,
    ShopifyConnectCallbackResponse,
    ShopifyConnectStartRequest,
    ShopifyConnectStartResponse,
)
from app.services.nango_service import NangoService, NangoServiceError
from app.services.store_connection_service import (
    StoreConnectionError,
    create_or_update_store_from_nango_connection,
)


router = APIRouter(tags=["auth"])
auth_router = APIRouter(prefix="/auth")
connect_router = APIRouter(prefix="/connect/shopify", tags=["shopify-connect"])


@auth_router.post("/dev-token", response_model=DevTokenResponse)
def create_dev_token(
    request: DevTokenRequest,
    db: Session = Depends(get_db),
) -> DevTokenResponse:
    settings = load_settings()
    if settings.environment.lower() in {"prod", "production"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dev token endpoint is disabled in production.",
        )
    user = get_or_create_app_user(db, email=request.email, name=request.name)
    db.commit()
    db.refresh(user)
    return DevTokenResponse(
        access_token=create_access_token(user, settings=settings),
        user=user,
    )


@auth_router.get("/me", response_model=AppUserRead)
def get_me(current_user: AppUser = Depends(get_current_user)) -> AppUser:
    return current_user


@connect_router.post("/start", response_model=ShopifyConnectStartResponse)
def start_shopify_connect(
    request: ShopifyConnectStartRequest | None = None,
    current_user: AppUser = Depends(get_current_user),
) -> ShopifyConnectStartResponse:
    settings = load_settings()
    request = request or ShopifyConnectStartRequest()
    auth_state = create_access_token(
        current_user,
        purpose="shopify_connect",
        ttl_minutes=30,
        settings=settings,
    )
    success_url = _append_query(
        request.success_url or _default_callback_url(settings, "callback"),
        {"auth_state": auth_state},
    )
    error_url = _append_query(
        request.error_url or _default_callback_url(settings, "callback"),
        {"auth_state": auth_state},
    )
    try:
        session = NangoService.from_settings(settings).start_shopify_connection(
            str(current_user.id),
            provider_config_key=settings.nango_provider_config_key,
            success_url=success_url,
            error_url=error_url,
        )
    except NangoServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not start Nango Shopify connection: {exc}",
        ) from exc

    connect_url = _extract_connect_url(session)
    if connect_url:
        connect_url = _append_query(connect_url, {"apiURL": settings.nango_base_url})

    return ShopifyConnectStartResponse(
        provider_config_key=settings.nango_provider_config_key,
        connect_url=connect_url,
        connect_session_token=_extract_connect_session_token(session),
        raw_session=session,
    )


@connect_router.get("/callback", response_model=ShopifyConnectCallbackResponse)
def shopify_connect_callback_get(
    connection_id: str | None = Query(default=None),
    connection_id_camel: str | None = Query(default=None, alias="connectionId"),
    nango_connection_id: str | None = Query(default=None),
    shopify_store_domain: str | None = Query(default=None),
    shop: str | None = Query(default=None),
    name: str | None = Query(default=None),
    auth_state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> ShopifyConnectCallbackResponse:
    if error:
        raise HTTPException(status_code=400, detail=f"Shopify OAuth failed: {error}")
    resolved_connection_id = connection_id or connection_id_camel or nango_connection_id
    if not resolved_connection_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Nango connection_id is required.",
        )
    user = _resolve_callback_user(db, auth_state=auth_state, authorization=authorization)
    return _complete_shopify_connection(
        db,
        user=user,
        connection_id=resolved_connection_id,
        shopify_store_domain=shopify_store_domain or shop,
        name=name,
    )


@connect_router.post("/callback", response_model=ShopifyConnectCallbackResponse)
def shopify_connect_callback_post(
    request: ShopifyConnectCallbackRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> ShopifyConnectCallbackResponse:
    user = _resolve_callback_user(
        db,
        auth_state=request.auth_state,
        authorization=authorization,
    )
    return _complete_shopify_connection(
        db,
        user=user,
        connection_id=request.connection_id,
        shopify_store_domain=request.shopify_store_domain,
        name=request.name,
    )


def _complete_shopify_connection(
    db: Session,
    *,
    user: AppUser,
    connection_id: str,
    shopify_store_domain: str | None,
    name: str | None,
) -> ShopifyConnectCallbackResponse:
    try:
        store, created = create_or_update_store_from_nango_connection(
            db,
            user=user,
            connection_id=connection_id,
            shopify_store_domain=shopify_store_domain,
            name=name,
        )
    except StoreConnectionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    return ShopifyConnectCallbackResponse(
        status="created" if created else "updated",
        created=created,
        store=store,
        next_sync_url=f"/stores/{store.id}/sync",
    )


def _resolve_callback_user(
    db: Session,
    *,
    auth_state: str | None,
    authorization: str | None,
) -> AppUser:
    if auth_state:
        payload = decode_token(auth_state, purpose="shopify_connect")
        user = db.get(AppUser, int(payload.get("sub") or 0))
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Connect callback user was not found.",
            )
        return user
    if authorization and authorization.lower().startswith("bearer "):
        payload = decode_token(authorization.split(" ", 1)[1].strip(), purpose="access")
        user = db.get(AppUser, int(payload.get("sub") or 0))
        if user:
            return user
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Connect callback requires auth_state or bearer auth.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _default_callback_url(settings, route_name: str) -> str:
    base_url = settings.public_app_url or "http://127.0.0.1:8010"
    return f"{base_url.rstrip('/')}/connect/shopify/{route_name}"


def _append_query(url: str, params: dict[str, str]) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _extract_connect_url(session: dict) -> str | None:
    for key in (
        "connect_url",
        "connectUrl",
        "connect_link",
        "connectLink",
        "url",
        "redirect_url",
        "redirectUrl",
    ):
        value = session.get(key)
        if isinstance(value, str):
            return value
    data = session.get("data")
    if isinstance(data, dict):
        return _extract_connect_url(data)
    return None


def _extract_connect_session_token(session: dict) -> str | None:
    for key in ("connect_session_token", "connectSessionToken", "token", "session_token"):
        value = session.get(key)
        if isinstance(value, str):
            return value
    data = session.get("data")
    if isinstance(data, dict):
        return _extract_connect_session_token(data)
    return None


router.include_router(auth_router)
router.include_router(connect_router)
