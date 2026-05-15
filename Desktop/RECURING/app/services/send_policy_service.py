from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RetentionCampaignState, RetentionSendLog


class SendPolicyError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def enforce_send_policy(
    db: Session,
    *,
    store_id: int,
    customer_id: int,
    campaign_type: str,
    force: bool = False,
) -> None:
    if force:
        return

    recent_send = db.scalar(
        select(RetentionSendLog)
        .where(
            RetentionSendLog.store_id == store_id,
            RetentionSendLog.customer_id == customer_id,
            RetentionSendLog.status == "sent",
            RetentionSendLog.sent_at >= utc_now() - timedelta(days=7),
        )
        .order_by(RetentionSendLog.sent_at.desc())
        .limit(1)
    )
    if recent_send:
        raise SendPolicyError(
            "Customer already received a retention message in the last 7 days.",
            status_code=409,
        )

    active_pre_churn = db.scalar(
        select(RetentionCampaignState)
        .where(
            RetentionCampaignState.store_id == store_id,
            RetentionCampaignState.customer_id == customer_id,
            RetentionCampaignState.campaign_type == "pre_churn",
            RetentionCampaignState.status == "active",
        )
        .limit(1)
    )
    if active_pre_churn and campaign_type != "pre_churn":
        raise SendPolicyError(
            "Customer is in pre-churn intervention; other feature sends are paused.",
            status_code=409,
        )


def record_retention_send(
    db: Session,
    *,
    store_id: int,
    customer_id: int,
    campaign_type: str,
    trigger_reason: str | None,
    subject: str,
    provider: str | None,
    provider_message_id: str | None,
    outfit_image_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> RetentionSendLog:
    row = RetentionSendLog(
        store_id=store_id,
        customer_id=customer_id,
        campaign_type=campaign_type,
        trigger_reason=trigger_reason,
        status="sent",
        subject=subject,
        provider=provider,
        provider_message_id=provider_message_id,
        outfit_image_id=outfit_image_id,
        sent_at=utc_now(),
        metadata_json=metadata or {},
    )
    db.add(row)
    db.flush()
    return row
