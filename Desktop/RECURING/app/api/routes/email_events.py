from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Customer, EmailEngagement, RetentionSendLog

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/email-events", tags=["email_events"])


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


SG_EVENT_MAP = {
    "delivered": "sent",
    "open": "open",
    "click": "click",
    "bounce": "bounce",
    "unsubscribe": "unsubscribe",
    "spamreport": "spam",
    "dropped": "dropped",
    "deferred": "deferred",
}


def process_sendgrid_events(events: list[dict[str, Any]]) -> None:
    db = SessionLocal()
    try:
        processed = 0
        for event in events:
            try:
                sg_event_type = event.get("event", "")
                internal_event_type = SG_EVENT_MAP.get(sg_event_type)
                if not internal_event_type:
                    continue

                email = event.get("email", "")
                sg_message_id = event.get("sg_message_id", "")
                timestamp = event.get("timestamp")
                url = event.get("url")
                campaign_type = event.get("campaign_type") or event.get(
                    "marketing_campaign_name"
                )

                occurred_at = (
                    datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
                    if timestamp
                    else utc_now()
                )

                send_log = None
                customer = None
                store_id = None
                customer_id = None

                if sg_message_id:
                    send_log = db.scalar(
                        select(RetentionSendLog).where(
                            RetentionSendLog.provider_message_id == sg_message_id
                        )
                    )

                if send_log:
                    store_id = send_log.store_id
                    customer_id = send_log.customer_id
                elif email:
                    customer = db.scalar(
                        select(Customer).where(Customer.email == email)
                    )
                    if customer:
                        store_id = customer.store_id
                        customer_id = customer.id

                if not store_id or not customer_id:
                    logger.debug(f"Skipping event - no matching customer: {email}")
                    continue

                engagement = EmailEngagement(
                    store_id=store_id,
                    customer_id=customer_id,
                    send_log_id=send_log.id if send_log else None,
                    campaign_type=campaign_type,
                    event_type=internal_event_type,
                    provider="sendgrid",
                    provider_message_id=sg_message_id,
                    url=url,
                    metadata_json=event,
                    timestamp=occurred_at,
                )
                db.add(engagement)
                processed += 1

            except Exception as e:
                logger.error(f"Error processing single event: {e}", exc_info=True)

        db.commit()
        logger.info(f"Processed {processed} SendGrid events")

    except Exception as e:
        logger.error(f"Error in process_sendgrid_events: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


@router.post("/sendgrid")
async def sendgrid_events(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    try:
        events = await request.json()
    except Exception:
        body = await request.body()
        events_str = body.decode("utf-8")
        events = []
        for line in events_str.split("&"):
            if "=" in line:
                events.append({"event": line.split("=")[0]})

    if not isinstance(events, list):
        events = [events]

    background_tasks.add_task(process_sendgrid_events, events=events)

    return {"status": "accepted", "events_received": len(events)}


@router.post("/sendgrid/test")
async def sendgrid_test(
    request: Request,
) -> dict:
    events = await request.json()
    return {
        "status": "test_ok",
        "events_received": len(events) if isinstance(events, list) else 1,
        "sample": events[0] if isinstance(events, list) and events else events,
    }
