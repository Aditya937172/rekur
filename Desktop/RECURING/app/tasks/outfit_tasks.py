from __future__ import annotations

import logging

from app.worker import celery_app
from app.db.session import SessionLocal
from app.core.observability import log_pipeline_event
from app.schemas import GenerateOutfitImageRequest
from app.services.outfit_service import generate_outfit_for_customer

logger = logging.getLogger(__name__)


def run_generate_and_send_outfit(
    *,
    store_id: int,
    customer_id: int,
    order_id: int | None,
    trigger_reason: str,
    recipient_email: str | None = None,
) -> dict:
    db = SessionLocal()
    try:
        log_pipeline_event(
            "trigger_received",
            pipeline="celery_outfit_task",
            store_id=store_id,
            customer_id=customer_id,
            order_id=order_id,
            trigger_reason=trigger_reason,
        )
        logger.info(f"Generating outfit for customer {customer_id} order {order_id}")
        request = GenerateOutfitImageRequest(
            customer_id=customer_id,
            order_id=order_id,
            trigger_reason=trigger_reason,
            send_email=bool(recipient_email),
            recipient_email=recipient_email,
        )
        result = generate_outfit_for_customer(db, store_id, request)
        logger.info(f"Outfit {result.id} generated, status={result.status}")
        return {"outfit_id": result.id, "status": result.status}
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def generate_and_send_outfit_task(
    self,
    store_id: int,
    customer_id: int,
    order_id: int | None,
    trigger_reason: str,
    recipient_email: str | None = None,
) -> dict:
    try:
        return run_generate_and_send_outfit(
            store_id=store_id,
            customer_id=customer_id,
            order_id=order_id,
            trigger_reason=trigger_reason,
            recipient_email=recipient_email,
        )
    except Exception as exc:
        logger.error(f"Outfit task failed: {exc}", exc_info=True)
        raise self.retry(exc=exc, countdown=min(60 * (2 ** self.request.retries), 900))


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def generate_seasonal_lookbook_task(
    self,
    store_id: int,
    customer_id: int,
    season: str,
    hemisphere: str,
    recipient_email: str | None = None,
) -> dict:
    db = SessionLocal()
    try:
        from app.services.seasonal_lookbook_service import generate_seasonal_lookbook
        from app.schemas.outfit import SeasonalLookbookRequest

        logger.info(f"Generating seasonal lookbook for customer {customer_id}")
        request = SeasonalLookbookRequest(
            customer_id=customer_id,
            season=season,
            hemisphere=hemisphere,
            send_email=bool(recipient_email),
            recipient_email=recipient_email,
        )
        result = generate_seasonal_lookbook(db, store_id, request)
        logger.info(f"Lookbook {result.id} generated, status={result.status}")
        return {"lookbook_id": result.id, "status": result.status}
    except Exception as exc:
        logger.error(f"Seasonal lookbook task failed: {exc}", exc_info=True)
        raise self.retry(exc=exc, countdown=min(60 * (2 ** self.request.retries), 900))
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def generate_anniversary_outfit_task(
    self,
    store_id: int,
    customer_id: int,
    days_window: int,
    recipient_email: str | None = None,
) -> dict:
    db = SessionLocal()
    try:
        from app.services.anniversary_service import run_first_order_anniversary_campaign
        from app.schemas.outfit import FirstOrderAnniversaryCampaignRequest

        logger.info(f"Generating anniversary outfit for customer {customer_id}")
        request = FirstOrderAnniversaryCampaignRequest(
            customer_id=customer_id,
            days_window=days_window,
            limit=1,
            send_email=bool(recipient_email),
            recipient_email=recipient_email,
            force=True,
        )
        result = run_first_order_anniversary_campaign(db, store_id, request)
        outfit = result.outfits[0] if result.outfits else None
        logger.info(
            "Anniversary task complete for customer %s: generated=%s sent=%s skipped=%s",
            customer_id,
            result.generated,
            result.sent,
            len(result.skipped),
        )
        return {
            "outfit_id": outfit.id if outfit else None,
            "status": outfit.status if outfit else "skipped",
            "processed": result.processed,
            "generated": result.generated,
            "sent": result.sent,
            "skipped": [item.model_dump() for item in result.skipped],
        }
    except Exception as exc:
        logger.error(f"Anniversary outfit task failed: {exc}", exc_info=True)
        raise self.retry(exc=exc, countdown=min(60 * (2 ** self.request.retries), 900))
    finally:
        db.close()
