from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EmailEngagement, RetentionCampaignState, RetentionSendLog


class SendPolicyError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


POST_PURCHASE_CAMPAIGN = "post_purchase_outfit"
PRE_CHURN_CAMPAIGN = "pre_churn"
PRE_CHURN_PAUSED_CAMPAIGNS = {
    "purchase_anniversary",
    "seasonal_lookbook",
    "silent_customer",
}


def enforce_send_policy(
    db: Session,
    *,
    store_id: int,
    customer_id: int,
    campaign_type: str,
    trigger_reason: str | None = None,
    reference_time: datetime | None = None,
    force: bool = False,
) -> None:
    if force:
        return

    now = reference_time or utc_now()
    campaign_type = campaign_type.strip().lower()
    trigger_reason = trigger_reason or None

    enforce_campaign_duplicate_policy(
        db,
        store_id=store_id,
        customer_id=customer_id,
        campaign_type=campaign_type,
        trigger_reason=trigger_reason,
        reference_time=now,
    )

    recent_send = db.scalar(
        select(RetentionSendLog)
        .where(
            RetentionSendLog.store_id == store_id,
            RetentionSendLog.customer_id == customer_id,
            RetentionSendLog.status == "sent",
            RetentionSendLog.sent_at >= now - timedelta(days=7),
        )
        .order_by(RetentionSendLog.sent_at.desc())
        .limit(1)
    )
    if recent_send and campaign_type != POST_PURCHASE_CAMPAIGN:
        raise SendPolicyError(
            (
                "Skipped: customer already received a retention message in the last "
                f"7 days ({recent_send.campaign_type})."
            ),
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
    if active_pre_churn and campaign_type in PRE_CHURN_PAUSED_CAMPAIGNS:
        raise SendPolicyError(
            (
                "Skipped: customer is in pre-churn intervention; anniversary, "
                "seasonal, and silent-customer campaigns are paused."
            ),
            status_code=409,
        )


def enforce_campaign_duplicate_policy(
    db: Session,
    *,
    store_id: int,
    customer_id: int,
    campaign_type: str,
    trigger_reason: str | None,
    reference_time: datetime,
) -> None:
    if campaign_type == "purchase_anniversary":
        if already_sent_anniversary_year(
            db,
            store_id=store_id,
            customer_id=customer_id,
            year=reference_time.year,
        ):
            raise SendPolicyError(
                f"Skipped: anniversary already sent for customer in {reference_time.year}.",
                status_code=409,
            )

    if campaign_type == "seasonal_lookbook":
        season_key = season_key_from_trigger(trigger_reason)
        if already_sent_seasonal(
            db,
            store_id=store_id,
            customer_id=customer_id,
            season_key=season_key,
            year=reference_time.year,
        ):
            raise SendPolicyError(
                f"Skipped: seasonal lookbook already sent for {season_key} {reference_time.year}.",
                status_code=409,
            )


def already_sent_anniversary_year(
    db: Session,
    *,
    store_id: int,
    customer_id: int,
    year: int,
) -> bool:
    year_start, next_year_start = year_bounds(year)
    return (
        db.scalar(
            select(RetentionSendLog.id)
            .where(
                RetentionSendLog.store_id == store_id,
                RetentionSendLog.customer_id == customer_id,
                RetentionSendLog.campaign_type == "purchase_anniversary",
                RetentionSendLog.status == "sent",
                RetentionSendLog.sent_at >= year_start,
                RetentionSendLog.sent_at < next_year_start,
            )
            .limit(1)
        )
        is not None
    )


def already_sent_seasonal(
    db: Session,
    *,
    store_id: int,
    customer_id: int,
    season_key: str,
    year: int,
) -> bool:
    year_start, next_year_start = year_bounds(year)
    trigger = f"seasonal_lookbook_{season_key}"
    return (
        db.scalar(
            select(RetentionSendLog.id)
            .where(
                RetentionSendLog.store_id == store_id,
                RetentionSendLog.customer_id == customer_id,
                RetentionSendLog.campaign_type == "seasonal_lookbook",
                RetentionSendLog.status == "sent",
                RetentionSendLog.sent_at >= year_start,
                RetentionSendLog.sent_at < next_year_start,
                RetentionSendLog.trigger_reason == trigger,
            )
            .limit(1)
        )
        is not None
    )


def season_key_from_trigger(trigger_reason: str | None) -> str:
    if trigger_reason and trigger_reason.startswith("seasonal_lookbook_"):
        return trigger_reason.replace("seasonal_lookbook_", "", 1).strip() or "current"
    return "current"


def year_bounds(year: int) -> tuple[datetime, datetime]:
    return (
        datetime(year, 1, 1, tzinfo=timezone.utc),
        datetime(year + 1, 1, 1, tzinfo=timezone.utc),
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
    sent_at: datetime | None = None,
) -> RetentionSendLog:
    timestamp = sent_at or utc_now()
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
        sent_at=timestamp,
        metadata_json=metadata or {},
    )
    db.add(row)
    db.flush()
    db.add(
        EmailEngagement(
            store_id=store_id,
            customer_id=customer_id,
            send_log_id=row.id,
            provider_message_id=provider_message_id,
            campaign_type=campaign_type,
            event_type="sent",
            metadata_json={
                "provider": provider,
                "trigger_reason": trigger_reason,
                "source": "retention_send_log",
                **(metadata or {}),
            },
            timestamp=timestamp,
        )
    )
    return row
