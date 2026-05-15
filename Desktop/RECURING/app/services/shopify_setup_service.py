from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.core.config import AppSettings, load_settings
from app.models import Store
from app.services.nango_service import NangoService, NangoServiceError


class ShopifySetupError(RuntimeError):
    pass


@dataclass
class TrackingInstallSummary:
    status: str
    store_id: int
    tracking_installed: bool
    tracking_installed_at: datetime | None
    script_url: str
    message: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def install_tracking_script(
    db: Session,
    store_id: int,
    *,
    settings: AppSettings | None = None,
    nango_service: NangoService | None = None,
) -> TrackingInstallSummary:
    settings = settings or load_settings()
    script_url = resolve_tracking_script_url(settings)
    store = db.get(Store, store_id)
    if not store:
        raise ShopifySetupError(f"Store {store_id} was not found.")

    print(f"Installing tracking for store {store.shopify_store_domain}")
    nango = nango_service or NangoService.from_settings(settings)

    try:
        existing = list_script_tags(nango, store.nango_connection_id)
        if has_tracker_script(existing, script_url):
            print("Tracking already installed")
            mark_tracking_installed(db, store)
            return TrackingInstallSummary(
                status="skipped",
                store_id=store.id,
                tracking_installed=True,
                tracking_installed_at=store.tracking_installed_at,
                script_url=script_url,
                message="Tracking already installed",
            )

        payload = {
            "script_tag": {
                "event": "onload",
                "src": script_url,
            }
        }
        nango.proxy_post(
            store.nango_connection_id,
            settings.nango_provider_config_key,
            f"admin/api/{settings.shopify_api_version}/script_tags.json",
            json=payload,
        )
        mark_tracking_installed(db, store)
        print("Tracking installed successfully")
        return TrackingInstallSummary(
            status="success",
            store_id=store.id,
            tracking_installed=True,
            tracking_installed_at=store.tracking_installed_at,
            script_url=script_url,
            message="Tracking installed successfully",
        )
    except NangoServiceError as exc:
        detail = exc.response_body or str(exc)
        print(f"Tracking install failed: {detail}")
        raise ShopifySetupError(
            "Tracking install failed via Shopify ScriptTag API. "
            "Confirm the Shopify OAuth app has read_script_tags and "
            f"write_script_tags scopes. Details: {detail}"
        ) from exc


def resolve_tracking_script_url(settings: AppSettings) -> str:
    script_url = settings.tracking_script_url
    if not script_url and settings.public_app_url:
        script_url = f"{settings.public_app_url.rstrip('/')}/public/tracker.js"
    if not script_url:
        raise ShopifySetupError(
            "TRACKING_SCRIPT_URL or PUBLIC_APP_URL is required. "
            "Use a public HTTPS URL, for example an ngrok URL."
        )

    parsed = urlparse(script_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise ShopifySetupError("Tracking script URL must use https.")
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        raise ShopifySetupError(
            "Tracking script URL must be public. Do not use localhost for Shopify ScriptTags."
        )
    if not script_url.endswith("/public/tracker.js"):
        raise ShopifySetupError("Tracking script URL must end with /public/tracker.js.")
    return script_url


def list_script_tags(
    nango: NangoService,
    connection_id: str,
) -> list[dict]:
    records: list[dict] = []
    params: dict[str, str | int] | None = {"limit": 250}
    while params is not None:
        response = nango._proxy_request(
            "GET",
            connection_id=connection_id,
            endpoint=f"admin/api/{nango.shopify_api_version}/script_tags.json",
            params=params,
        )
        payload = nango._json_response(response)
        page_records = payload.get("script_tags", [])
        if not isinstance(page_records, list):
            raise ShopifySetupError("Shopify script_tags response was not a list.")
        records.extend(page_records)
        params = nango._next_page_params(response.headers.get("link"))
    return records


def has_tracker_script(script_tags: list[dict], script_url: str) -> bool:
    for script_tag in script_tags:
        src = str(script_tag.get("src") or "")
        if src == script_url or src.endswith("/public/tracker.js"):
            return True
    return False


def mark_tracking_installed(db: Session, store: Store) -> None:
    store.tracking_installed = True
    store.tracking_installed_at = utc_now()
    db.commit()
    db.refresh(store)
