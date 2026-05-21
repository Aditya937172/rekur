from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.session import get_db
from app.models import AppUser
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
    customer_reply_id: int | None = None
    signals: dict | None = None
    acknowledgment_status: str | None = None


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
    _current_user: AppUser = Depends(get_current_user),
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
        customer_reply_id=result.get("customer_reply_id"),
        signals=result.get("signals"),
        acknowledgment_status=result.get("acknowledgment_status"),
    )


@router.post("/poll-gmail")
async def poll_gmail_replies_now(
    _current_user: AppUser = Depends(get_current_user),
) -> dict:
    from app.services.campaign_orchestrator import poll_gmail_for_replies_all_stores

    try:
        return await poll_gmail_for_replies_all_stores()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Gmail reply polling failed: {exc}",
        ) from exc
