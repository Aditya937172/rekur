from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import ensure_user_owns_store, get_current_user
from app.core.observability import capture_exception, log_pipeline_error, log_pipeline_event
from app.db.session import SessionLocal, get_db
from app.models import AppUser, Customer, EmailEngagement, RetentionSendLog
from app.schemas import EmailEngagementCreate, EmailEngagementResponse
from app.services.retention_data_service import (
    RetentionDataServiceError,
    record_email_engagement,
)

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


class GmailManualEngagementRequest(BaseModel):
    store_id: int
    event_type: str = Field(max_length=64)
    email: str | None = Field(default=None, max_length=320)
    customer_id: int | None = None
    send_log_id: int | None = None
    provider_message_id: str | None = Field(default=None, max_length=255)
    campaign_type: str | None = Field(default=None, max_length=128)
    url: str | None = None
    timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


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
                    campaign_type = campaign_type or send_log.campaign_type
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
                log_pipeline_event(
                    "engagement_received",
                    pipeline="sendgrid_email_event",
                    provider="sendgrid",
                    store_id=store_id,
                    customer_id=customer_id,
                    event_type=internal_event_type,
                    provider_message_id=sg_message_id,
                )

                engagement = EmailEngagement(
                    store_id=store_id,
                    customer_id=customer_id,
                    send_log_id=send_log.id if send_log else None,
                    campaign_type=campaign_type,
                    event_type=internal_event_type,
                    provider_message_id=sg_message_id,
                    url=url,
                    metadata_json={**event, "provider": "sendgrid"},
                    timestamp=occurred_at,
                )
                db.add(engagement)
                processed += 1

            except Exception as e:
                logger.error(f"Error processing single event: {e}", exc_info=True)
                log_pipeline_error(
                    "engagement_processing_failed",
                    e,
                    pipeline="sendgrid_email_event",
                    provider="sendgrid",
                )
                capture_exception(e, pipeline="sendgrid_email_event")

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
    log_pipeline_event(
        "trigger_received",
        pipeline="sendgrid_email_event",
        provider="sendgrid",
    )
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


@router.post("/gmail/test", response_model=EmailEngagementResponse)
async def gmail_test_event(
    payload: GmailManualEngagementRequest,
    current_user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmailEngagementResponse:
    try:
        ensure_user_owns_store(db, current_user.id, payload.store_id)
        log_pipeline_event(
            "engagement_received",
            pipeline="gmail_manual_email_event",
            provider="gmail",
            store_id=payload.store_id,
            customer_id=payload.customer_id,
            event_type=payload.event_type,
            provider_message_id=payload.provider_message_id,
        )
        return record_email_engagement(
            db,
            payload.store_id,
            EmailEngagementCreate(
                customer_id=payload.customer_id,
                email=payload.email,
                send_log_id=payload.send_log_id,
                provider="gmail",
                provider_message_id=payload.provider_message_id,
                campaign_type=payload.campaign_type,
                event_type=payload.event_type,
                url=payload.url,
                timestamp=payload.timestamp,
                metadata={**payload.metadata, "source": "gmail_manual_test"},
            ),
        )
    except RetentionDataServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
