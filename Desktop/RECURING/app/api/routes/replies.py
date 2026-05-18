from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.reply_processor import process_customer_reply

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/replies", tags=["replies"])


class InboundReplyRequest(BaseModel):
    customer_email: str = Field(..., max_length=320)
    reply_text: str = Field(..., min_length=1, max_length=5000)
    subject: str | None = Field(default=None, max_length=500)
    message_id: str | None = Field(default=None, max_length=255)


class InboundReplyResponse(BaseModel):
    status: str
    customer_id: int | None = None
    signals: dict | None = None


def process_reply_async(
    customer_email: str,
    reply_text: str,
    subject: str | None,
    message_id: str | None,
) -> None:
    from app.db.session import SessionLocal
    from app.core.config import load_settings

    db = SessionLocal()
    try:
        process_customer_reply(
            db,
            customer_email=customer_email,
            reply_text=reply_text,
            subject=subject,
            message_id=message_id,
            settings=load_settings(),
            send_acknowledgment=True,
        )
    except Exception as e:
        logger.error(f"Async reply processing failed: {e}", exc_info=True)
    finally:
        db.close()


@router.post("/inbound", response_model=InboundReplyResponse)
async def receive_inbound_reply(
    request: InboundReplyRequest,
    background_tasks: BackgroundTasks,
) -> InboundReplyResponse:
    background_tasks.add_task(
        process_reply_async,
        customer_email=request.customer_email,
        reply_text=request.reply_text,
        subject=request.subject,
        message_id=request.message_id,
    )

    return InboundReplyResponse(
        status="accepted",
    )


@router.post("/process", response_model=InboundReplyResponse)
async def process_reply_sync(
    request: InboundReplyRequest,
    db: Session = Depends(get_db),
) -> InboundReplyResponse:
    from app.core.config import load_settings

    result = process_customer_reply(
        db,
        customer_email=request.customer_email,
        reply_text=request.reply_text,
        subject=request.subject,
        message_id=request.message_id,
        settings=load_settings(),
        send_acknowledgment=True,
    )

    return InboundReplyResponse(
        status=result.get("status", "unknown"),
        customer_id=result.get("customer_id"),
        signals=result.get("signals"),
    )
