from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import Store
from app.schemas import CampaignRunRequest

logger = logging.getLogger(__name__)


def get_all_active_store_ids(db: Session) -> list[int]:
    stores = db.scalars(
        select(Store.id).where(Store.nango_connection_id.isnot(None))
    ).all()
    return list(stores)


def get_all_active_stores(db: Session) -> list[Store]:
    return list(
        db.scalars(select(Store).where(Store.nango_connection_id.isnot(None))).all()
    )


async def run_seasonal_for_all_stores(
    season: str, hemisphere: str, limit: int = 500
) -> dict:
    from app.services.seasonal_scheduler import run_quarterly_seasonal_campaign
    from app.utils.season_utils import Hemisphere

    db = SessionLocal()
    results = {}
    try:
        store_ids = get_all_active_store_ids(db)
        logger.info(
            f"Running {season} campaign for {len(store_ids)} stores ({hemisphere})"
        )

        hemi = Hemisphere.NORTHERN if hemisphere == "northern" else Hemisphere.SOUTHERN

        for store_id in store_ids:
            try:
                result = await run_quarterly_seasonal_campaign(
                    db,
                    store_id=store_id,
                    hemisphere=hemi,
                    limit=limit,
                )
                results[store_id] = result
                logger.info(f"Store {store_id}: {result}")
            except Exception as e:
                logger.error(
                    f"Seasonal campaign failed for store {store_id}: {e}", exc_info=True
                )
                results[store_id] = {"error": str(e)}
    finally:
        db.close()

    return results


async def run_pre_churn_for_all_stores(limit: int = 200) -> dict:
    from app.services.retention_campaign_service import run_pre_churn_campaign

    db = SessionLocal()
    results = {}
    try:
        store_ids = get_all_active_store_ids(db)
        logger.info(f"Running pre-churn for {len(store_ids)} stores")

        for store_id in store_ids:
            try:
                request = CampaignRunRequest(limit=limit, send_email=True)
                result = run_pre_churn_campaign(db, store_id, request)
                results[store_id] = {
                    "processed": result.processed,
                    "sent": result.sent,
                    "skipped": result.skipped,
                }
                logger.info(f"Store {store_id}: pre-churn sent={result.sent}")
            except Exception as e:
                logger.error(
                    f"Pre-churn failed for store {store_id}: {e}", exc_info=True
                )
                results[store_id] = {"error": str(e)}
    finally:
        db.close()

    return results


async def run_silent_customer_for_all_stores(limit: int = 200) -> dict:
    from app.services.retention_campaign_service import run_silent_customer_campaign

    db = SessionLocal()
    results = {}
    try:
        store_ids = get_all_active_store_ids(db)
        logger.info(f"Running silent customer campaign for {len(store_ids)} stores")

        for store_id in store_ids:
            try:
                request = CampaignRunRequest(limit=limit, send_email=True)
                result = run_silent_customer_campaign(db, store_id, request)
                results[store_id] = {
                    "processed": result.processed,
                    "sent": result.sent,
                    "skipped": result.skipped,
                }
                logger.info(f"Store {store_id}: silent sent={result.sent}")
            except Exception as e:
                logger.error(
                    f"Silent customer campaign failed for store {store_id}: {e}",
                    exc_info=True,
                )
                results[store_id] = {"error": str(e)}
    finally:
        db.close()

    return results


async def run_anniversary_for_all_stores(
    days_window: int = 365, limit: int = 100
) -> dict:
    from app.services.anniversary_service import run_anniversary_campaign

    db = SessionLocal()
    results = {}
    try:
        store_ids = get_all_active_store_ids(db)
        logger.info(f"Running anniversary campaign for {len(store_ids)} stores")

        for store_id in store_ids:
            try:
                result = run_anniversary_campaign(
                    db, store_id, days_window=days_window, limit=limit
                )
                results[store_id] = result
                logger.info(f"Store {store_id}: anniversary {result}")
            except Exception as e:
                logger.error(
                    f"Anniversary campaign failed for store {store_id}: {e}",
                    exc_info=True,
                )
                results[store_id] = {"error": str(e)}
    finally:
        db.close()

    return results


async def run_seasonal_lookbook_for_all_stores(season: str, limit: int = 500) -> dict:
    from app.services.retention_campaign_service import run_seasonal_lookbook_campaign

    db = SessionLocal()
    results = {}
    try:
        store_ids = get_all_active_store_ids(db)
        logger.info(f"Running {season} lookbook for {len(store_ids)} stores")

        for store_id in store_ids:
            try:
                request = CampaignRunRequest(limit=limit, send_email=True)
                result = run_seasonal_lookbook_campaign(
                    db, store_id, request, season=season
                )
                results[store_id] = {
                    "processed": result.processed,
                    "sent": result.sent,
                    "skipped": result.skipped,
                }
                logger.info(f"Store {store_id}: {season} sent={result.sent}")
            except Exception as e:
                logger.error(
                    f"Seasonal lookbook failed for store {store_id}: {e}", exc_info=True
                )
                results[store_id] = {"error": str(e)}
    finally:
        db.close()

    return results


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
            except Exception as e:
                logger.error(f"Failed to process reply from {reply['from_email']}: {e}")
                results["errors"] += 1
    finally:
        db.close()

    logger.info(f"Gmail reply polling complete: {results}")
    return results


async def nightly_shopify_sync_all_stores() -> dict:
    from app.services.shopify_sync_service import full_sync_for_store

    db = SessionLocal()
    results = {}
    try:
        store_ids = get_all_active_store_ids(db)
        logger.info(f"Running nightly Shopify sync for {len(store_ids)} stores")

        for store_id in store_ids:
            try:
                sync_result = full_sync_for_store(store_id)
                results[store_id] = sync_result
                logger.info(f"Store {store_id} sync: {sync_result}")
            except Exception as e:
                logger.error(
                    f"Nightly sync failed for store {store_id}: {e}", exc_info=True
                )
                results[store_id] = {"error": str(e)}
    finally:
        db.close()

    logger.info(f"Nightly sync complete for {len(results)} stores")
    return results
