from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import add_store_owner
from app.core.config import AppSettings, load_settings
from app.models import AppUser, Store
from app.services.nango_service import NangoService, NangoServiceError


class StoreConnectionError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def create_or_update_store_from_nango_connection(
    db: Session,
    *,
    user: AppUser,
    connection_id: str,
    shopify_store_domain: str | None = None,
    name: str | None = None,
    settings: AppSettings | None = None,
) -> tuple[Store, bool]:
    settings = settings or load_settings()
    connection_payload: dict[str, Any] = {}
    if settings.nango_secret_key:
        connection_payload = _fetch_nango_connection(connection_id, settings)
        _assert_connection_belongs_to_user(connection_payload, user)
        connection_domain = _extract_shopify_domain(connection_payload)
        supplied_domain = normalize_shopify_domain(shopify_store_domain)
        if connection_domain and supplied_domain and connection_domain != supplied_domain:
            raise StoreConnectionError(
                "Nango connection domain does not match supplied Shopify domain.",
                status_code=status.HTTP_409_CONFLICT,
            )
        shopify_store_domain = connection_domain or supplied_domain
    elif not shopify_store_domain and settings.environment.lower() in {"prod", "production"}:
        raise StoreConnectionError(
            "NANGO_SECRET_KEY is required to verify Shopify connections in production.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    normalized_domain = normalize_shopify_domain(shopify_store_domain)
    if not normalized_domain:
        raise StoreConnectionError(
            "Shopify store domain was not found. Pass shopify_store_domain from the OAuth callback.",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    store = db.scalar(
        select(Store).where(
            (Store.nango_connection_id == connection_id)
            | (Store.shopify_store_domain == normalized_domain)
        )
    )
    created = False
    if store:
        store.nango_connection_id = connection_id
        store.shopify_store_domain = normalized_domain
        if name:
            store.name = name
    else:
        store = Store(
            name=name or _default_store_name(normalized_domain),
            nango_connection_id=connection_id,
            shopify_store_domain=normalized_domain,
        )
        db.add(store)
        created = True

    try:
        db.flush()
        add_store_owner(db, user_id=user.id, store_id=store.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise StoreConnectionError(
            "A different store already uses this Nango connection or Shopify domain.",
            status_code=status.HTTP_409_CONFLICT,
        ) from exc
    db.refresh(store)
    return store, created


def normalize_shopify_domain(domain: str | None) -> str:
    if not domain:
        return ""
    clean = (
        domain.replace("https://", "")
        .replace("http://", "")
        .split("/", 1)[0]
        .strip()
        .lower()
    )
    if clean and "." not in clean:
        clean = f"{clean}.myshopify.com"
    return clean


def _fetch_nango_connection(
    connection_id: str,
    settings: AppSettings,
) -> dict[str, Any]:
    try:
        return NangoService.from_settings(settings).get_connection(
            connection_id,
            settings.nango_provider_config_key,
        )
    except NangoServiceError as exc:
        raise StoreConnectionError(
            f"Could not read Nango connection {connection_id}: {exc}",
            status_code=status.HTTP_502_BAD_GATEWAY,
        ) from exc


def _assert_connection_belongs_to_user(payload: dict[str, Any], user: AppUser) -> None:
    end_user_id = _extract_end_user_id(payload)
    if not end_user_id:
        return
    allowed_values = {str(user.id), user.email}
    if end_user_id not in allowed_values:
        raise StoreConnectionError(
            "Nango connection belongs to a different app user.",
            status_code=status.HTTP_403_FORBIDDEN,
        )


def _extract_end_user_id(payload: dict[str, Any]) -> str:
    for key in ("end_user_id", "endUserId", "end_user", "endUser"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            nested = value.get("id") or value.get("email")
            if isinstance(nested, str):
                return nested
    return ""


def _extract_shopify_domain(payload: dict[str, Any]) -> str:
    direct_keys = {
        "shop",
        "shop_name",
        "shopName",
        "shopify_store_domain",
        "shopifyStoreDomain",
        "store_domain",
        "storeDomain",
        "domain",
        "subdomain",
    }
    for key in direct_keys:
        value = payload.get(key)
        if isinstance(value, str):
            normalized = normalize_shopify_domain(value)
            if normalized:
                return normalized

    nested_candidates = [
        payload.get("connection_config"),
        payload.get("connectionConfig"),
        payload.get("metadata"),
        payload.get("end_user"),
        payload.get("endUser"),
        payload.get("credentials"),
    ]
    for candidate in nested_candidates:
        found = _find_domain_recursively(candidate)
        if found:
            return found
    return _find_domain_recursively(payload)


def _find_domain_recursively(value: Any) -> str:
    if isinstance(value, str):
        normalized = normalize_shopify_domain(value)
        if ".myshopify.com" in normalized:
            return normalized
        return ""
    if isinstance(value, dict):
        for nested_value in value.values():
            found = _find_domain_recursively(nested_value)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_domain_recursively(item)
            if found:
                return found
    return ""


def _default_store_name(domain: str) -> str:
    return domain.replace(".myshopify.com", "").replace("-", " ").title()


def connection_error_to_http(exc: StoreConnectionError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))
