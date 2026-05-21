from __future__ import annotations

from typing import Any

from app.core.config import AppSettings
from app.db.session import SessionLocal
from app.services.nango_service import NangoService
from app.services.sync_service import sync_store


def full_sync_for_store(store_id: int, *, settings: AppSettings | None = None) -> dict[str, Any]:
    """Run the canonical Nango-backed full Shopify sync for one store."""
    db = SessionLocal()
    try:
        nango = NangoService.from_settings(settings)
        summary = sync_store(db, store_id, nango_service=nango)
        return {
            "store_id": store_id,
            "status": summary.status,
            "products_synced": summary.products_synced,
            "customers_synced": summary.customers_synced,
            "orders_synced": summary.orders_synced,
        }
    finally:
        db.close()


def sync_products_for_store(
    store_id: int,
    *,
    settings: AppSettings | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper. Product sync is part of the Nango full sync."""
    result = full_sync_for_store(store_id, settings=settings)
    return {
        "store_id": store_id,
        "status": result["status"],
        "products_synced": result["products_synced"],
        "note": "Products were synced through the Nango per-store connection.",
    }


def sync_customers_for_store(
    store_id: int,
    *,
    settings: AppSettings | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper. Customer sync is part of the Nango full sync."""
    result = full_sync_for_store(store_id, settings=settings)
    return {
        "store_id": store_id,
        "status": result["status"],
        "customers_synced": result["customers_synced"],
        "note": "Customers were synced through the Nango per-store connection.",
    }
