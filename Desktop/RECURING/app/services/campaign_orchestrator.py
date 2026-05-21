from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, is_dataclass
from inspect import isawaitable
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import load_settings
from app.db.session import SessionLocal
from app.models import Store
from app.schemas import CampaignRunRequest, FirstOrderAnniversaryCampaignRequest

logger = logging.getLogger(__name__)


def get_all_active_store_ids(db: Session) -> list[int]:
    stores = db.scalars(
        select(Store.id)
        .where(
            Store.nango_connection_id.isnot(None),
            Store.nango_connection_id != "",
            Store.shopify_store_domain.isnot(None),
            Store.shopify_store_domain != "",
        )
        .order_by(Store.id.asc())
    ).all()
    return list(stores)


def get_all_active_stores(db: Session) -> list[Store]:
    return list(
        db.scalars(
            select(Store)
            .where(
                Store.nango_connection_id.isnot(None),
                Store.nango_connection_id != "",
                Store.shopify_store_domain.isnot(None),
                Store.shopify_store_domain != "",
            )
            .order_by(Store.id.asc())
        ).all()
    )


StoreJob = Callable[[Session, int], dict[str, Any] | Awaitable[dict[str, Any]]]


def active_store_ids_snapshot() -> list[int]:
    db = SessionLocal()
    try:
        return get_all_active_store_ids(db)
    finally:
        db.close()


async def run_for_each_active_store(
    job_name: str,
    store_job: StoreJob,
) -> dict[int, dict[str, Any]]:
    store_ids = active_store_ids_snapshot()
    logger.info("Running %s for %s active stores", job_name, len(store_ids))

    results: dict[int, dict[str, Any]] = {}
    for store_id in store_ids:
        db = SessionLocal()
        try:
            logger.info("%s started for store %s", job_name, store_id)
            result = store_job(db, store_id)
            if isawaitable(result):
                result = await result
            db.commit()
            results[store_id] = {"status": "success", **normalize_result(result)}
            logger.info("%s complete for store %s: %s", job_name, store_id, result)
        except Exception as exc:
            db.rollback()
            logger.error(
                "%s failed for store %s: %s",
                job_name,
                store_id,
                exc,
                exc_info=True,
            )
            results[store_id] = {"status": "failed", "error": str(exc)}
        finally:
            db.close()

    return results


def normalize_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if is_dataclass(result):
        return asdict(result)
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if hasattr(result, "dict"):
        return result.dict()
    return {"result": str(result)}


async def run_seasonal_for_all_stores(
    season: str, hemisphere: str, limit: int = 500
) -> dict:
    from app.services.seasonal_scheduler import run_quarterly_seasonal_campaign
    from app.utils.season_utils import Hemisphere, Season

    hemi = Hemisphere.NORTHERN if hemisphere == "northern" else Hemisphere.SOUTHERN
    campaign_season = Season(season)

    async def job(db: Session, store_id: int) -> dict[str, Any]:
        return await run_quarterly_seasonal_campaign(
            db,
            store_id=store_id,
            hemisphere=hemi,
            season=campaign_season,
            limit=limit,
        )

    return await run_for_each_active_store(
        f"seasonal_{season}_{hemisphere}",
        job,
    )


async def run_pre_churn_for_all_stores(limit: int = 200) -> dict:
    from app.services.retention_campaign_service import run_pre_churn_campaign

    def job(db: Session, store_id: int) -> dict[str, Any]:
        request = CampaignRunRequest(limit=limit, send_email=True)
        result = run_pre_churn_campaign(db, store_id, request)
        return {
            "processed": result.processed,
            "generated": result.generated,
            "sent": result.sent,
            "skipped": result.skipped,
        }

    return await run_for_each_active_store("daily_pre_churn", job)


async def run_silent_customer_for_all_stores(limit: int = 200) -> dict:
    from app.services.retention_campaign_service import run_silent_customer_campaign

    def job(db: Session, store_id: int) -> dict[str, Any]:
        request = CampaignRunRequest(limit=limit, send_email=True)
        result = run_silent_customer_campaign(db, store_id, request)
        return {
            "processed": result.processed,
            "generated": result.generated,
            "sent": result.sent,
            "skipped": result.skipped,
        }

    return await run_for_each_active_store("daily_silent_customer", job)


async def run_anniversary_for_all_stores(days_window: int = 7, limit: int = 100) -> dict:
    from app.services.anniversary_service import run_first_order_anniversary_campaign

    def job(db: Session, store_id: int) -> dict[str, Any]:
        request = FirstOrderAnniversaryCampaignRequest(
            days_window=days_window,
            limit=limit,
            send_email=True,
        )
        result = run_first_order_anniversary_campaign(db, store_id, request)
        return {
            "processed": result.processed,
            "generated": result.generated,
            "sent": result.sent,
            "skipped": len(result.skipped),
        }

    return await run_for_each_active_store("daily_anniversary", job)


async def run_seasonal_lookbook_for_all_stores(season: str, limit: int = 500) -> dict:
    from app.services.retention_campaign_service import run_seasonal_lookbook_campaign

    def job(db: Session, store_id: int) -> dict[str, Any]:
        request = CampaignRunRequest(limit=limit, send_email=True)
        result = run_seasonal_lookbook_campaign(db, store_id, request, season=season)
        return {
            "processed": result.processed,
            "generated": result.generated,
            "sent": result.sent,
            "skipped": result.skipped,
        }

    return await run_for_each_active_store(f"seasonal_lookbook_{season}", job)


async def poll_gmail_for_replies_all_stores() -> dict:
    from app.services.gmail_service import list_recent_replies, mark_message_as_read
    from app.services.reply_processor import process_customer_reply

    settings = load_settings()
    replies = list_recent_replies(settings=settings)

    results = {"processed": 0, "skipped": 0, "errors": 0}
    db = SessionLocal()

    try:
        for reply in replies:
            try:
                result = process_customer_reply(
                    db,
                    customer_email=reply["from_email"],
                    reply_text=reply["body"],
                    subject=reply.get("subject"),
                    message_id=reply.get("message_id"),
                    settings=settings,
                    send_acknowledgment=True,
                )
                if result.get("status") == "processed":
                    results["processed"] += 1
                    mark_message_as_read(reply["message_id"], settings=settings)
                else:
                    results["skipped"] += 1
                    mark_message_as_read(reply["message_id"], settings=settings)
            except Exception as e:
                logger.error(f"Failed to process reply from {reply['from_email']}: {e}")
                results["errors"] += 1
    finally:
        db.close()

    logger.info(f"Gmail reply polling complete: {results}")
    return results


async def nightly_shopify_sync_all_stores() -> dict:
    from app.services.sync_service import sync_store

    def job(db: Session, store_id: int) -> dict[str, Any]:
        summary = sync_store(db, store_id)
        return {
            "sync_status": summary.status,
            "products_synced": summary.products_synced,
            "customers_synced": summary.customers_synced,
            "orders_synced": summary.orders_synced,
        }

    return await run_for_each_active_store("nightly_shopify_sync", job)
