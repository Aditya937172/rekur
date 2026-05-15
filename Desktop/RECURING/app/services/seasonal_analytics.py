"""
Tracking and analytics for seasonal lookbook campaigns.
Tracks saves, shares, gap link clicks, and engagement metrics.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    select,
)
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Customer, GeneratedOutfitImage, Store


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SeasonalCampaignAnalytics(Base):
    """Track analytics for seasonal lookbook campaigns."""

    __tablename__ = "seasonal_campaign_analytics"
    __table_args__ = (
        Index("ix_seasonal_analytics_store_season", "store_id", "season", "year"),
        Index("ix_seasonal_analytics_customer", "customer_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    customer_id = Column(
        Integer, ForeignKey("customers.id"), nullable=False, index=True
    )
    outfit_image_id = Column(
        Integer, ForeignKey("generated_outfit_images.id"), nullable=True
    )
    season = Column(String(32), nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)
    hemisphere = Column(String(32), nullable=False, default="northern")

    email_sent_at = Column(DateTime(timezone=True), nullable=True)
    email_opened_at = Column(DateTime(timezone=True), nullable=True)
    email_clicked_at = Column(DateTime(timezone=True), nullable=True)

    image_saved_count = Column(Integer, default=0, nullable=False)
    image_shared_count = Column(Integer, default=0, nullable=False)

    gap_link_clicked_at = Column(DateTime(timezone=True), nullable=True)
    gap_link_click_count = Column(Integer, default=0, nullable=False)
    gap_product_viewed_at = Column(DateTime(timezone=True), nullable=True)
    gap_product_added_to_cart_at = Column(DateTime(timezone=True), nullable=True)
    gap_product_purchased_at = Column(DateTime(timezone=True), nullable=True)

    engagement_score = Column(Float, default=0.0, nullable=False)

    metadata_json = Column("metadata", JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class CampaignEngagementEvent(Base):
    """Individual engagement events for detailed tracking."""

    __tablename__ = "campaign_engagement_events"
    __table_args__ = (
        Index("ix_engagement_events_store_time", "store_id", "created_at"),
        Index("ix_engagement_events_outfit", "outfit_image_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    outfit_image_id = Column(
        Integer, ForeignKey("generated_outfit_images.id"), nullable=True
    )

    event_type = Column(String(64), nullable=False, index=True)
    event_source = Column(String(64), nullable=True)

    url = Column(Text, nullable=True)
    referrer = Column(Text, nullable=True)
    user_agent = Column(Text, nullable=True)
    ip_address = Column(String(64), nullable=True)

    metadata_json = Column("metadata", JSON, nullable=False, default=dict)

    created_at = Column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


EVENT_TYPES = {
    "email_sent": "Email sent to customer",
    "email_opened": "Email opened by customer",
    "email_clicked": "Link clicked in email",
    "image_viewed": "Lookbook image viewed",
    "image_saved": "Customer saved image",
    "image_shared": "Customer shared image",
    "gap_link_clicked": "Gap recommendation link clicked",
    "gap_product_viewed": "Gap product page viewed",
    "gap_product_added_to_cart": "Gap product added to cart",
    "gap_product_purchased": "Gap product purchased",
}


def track_seasonal_campaign_sent(
    db: Session,
    *,
    store_id: int,
    customer_id: int,
    outfit_image_id: int,
    season: str,
    year: int,
    hemisphere: str = "northern",
) -> SeasonalCampaignAnalytics:
    """Initialize analytics record when campaign is sent."""
    analytics = db.scalar(
        select(SeasonalCampaignAnalytics).where(
            SeasonalCampaignAnalytics.store_id == store_id,
            SeasonalCampaignAnalytics.customer_id == customer_id,
            SeasonalCampaignAnalytics.season == season,
            SeasonalCampaignAnalytics.year == year,
        )
    )

    if not analytics:
        analytics = SeasonalCampaignAnalytics(
            store_id=store_id,
            customer_id=customer_id,
            outfit_image_id=outfit_image_id,
            season=season,
            year=year,
            hemisphere=hemisphere,
            email_sent_at=utc_now(),
        )
        db.add(analytics)
    else:
        analytics.email_sent_at = utc_now()

    db.flush()

    track_event(
        db,
        store_id=store_id,
        customer_id=customer_id,
        outfit_image_id=outfit_image_id,
        event_type="email_sent",
        event_source="seasonal_lookbook",
    )

    return analytics


def track_email_opened(
    db: Session,
    *,
    outfit_image_id: int,
    metadata: Optional[dict] = None,
) -> Optional[SeasonalCampaignAnalytics]:
    """Track when customer opens the email."""
    outfit = db.get(GeneratedOutfitImage, outfit_image_id)
    if not outfit:
        return None

    analytics = db.scalar(
        select(SeasonalCampaignAnalytics).where(
            SeasonalCampaignAnalytics.outfit_image_id == outfit_image_id
        )
    )

    if analytics:
        analytics.email_opened_at = utc_now()
        analytics.updated_at = utc_now()
        db.flush()

    track_event(
        db,
        store_id=outfit.store_id,
        customer_id=outfit.customer_id,
        outfit_image_id=outfit_image_id,
        event_type="email_opened",
        event_source="email_client",
        metadata=metadata,
    )

    return analytics


def track_link_clicked(
    db: Session,
    *,
    outfit_image_id: int,
    url: Optional[str] = None,
    is_gap_link: bool = False,
    metadata: Optional[dict] = None,
) -> Optional[SeasonalCampaignAnalytics]:
    """Track when customer clicks a link in the email."""
    outfit = db.get(GeneratedOutfitImage, outfit_image_id)
    if not outfit:
        return None

    analytics = db.scalar(
        select(SeasonalCampaignAnalytics).where(
            SeasonalCampaignAnalytics.outfit_image_id == outfit_image_id
        )
    )

    if analytics:
        analytics.email_clicked_at = utc_now()

        if is_gap_link:
            analytics.gap_link_clicked_at = utc_now()
            analytics.gap_link_click_count = (analytics.gap_link_click_count or 0) + 1

        analytics.updated_at = utc_now()
        db.flush()

    event_type = "gap_link_clicked" if is_gap_link else "email_clicked"

    track_event(
        db,
        store_id=outfit.store_id,
        customer_id=outfit.customer_id,
        outfit_image_id=outfit_image_id,
        event_type=event_type,
        event_source="email_link",
        url=url,
        metadata=metadata,
    )

    return analytics


def track_image_saved(
    db: Session,
    *,
    outfit_image_id: int,
    metadata: Optional[dict] = None,
) -> Optional[SeasonalCampaignAnalytics]:
    """Track when customer saves the lookbook image."""
    outfit = db.get(GeneratedOutfitImage, outfit_image_id)
    if not outfit:
        return None

    analytics = db.scalar(
        select(SeasonalCampaignAnalytics).where(
            SeasonalCampaignAnalytics.outfit_image_id == outfit_image_id
        )
    )

    if analytics:
        analytics.image_saved_count = (analytics.image_saved_count or 0) + 1
        analytics.updated_at = utc_now()
        db.flush()

    track_event(
        db,
        store_id=outfit.store_id,
        customer_id=outfit.customer_id,
        outfit_image_id=outfit_image_id,
        event_type="image_saved",
        event_source="email_client",
        metadata=metadata,
    )

    return analytics


def track_image_shared(
    db: Session,
    *,
    outfit_image_id: int,
    platform: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Optional[SeasonalCampaignAnalytics]:
    """Track when customer shares the lookbook image."""
    outfit = db.get(GeneratedOutfitImage, outfit_image_id)
    if not outfit:
        return None

    analytics = db.scalar(
        select(SeasonalCampaignAnalytics).where(
            SeasonalCampaignAnalytics.outfit_image_id == outfit_image_id
        )
    )

    if analytics:
        analytics.image_shared_count = (analytics.image_shared_count or 0) + 1
        analytics.updated_at = utc_now()
        db.flush()

    event_metadata = {"platform": platform} if platform else {}
    if metadata:
        event_metadata.update(metadata)

    track_event(
        db,
        store_id=outfit.store_id,
        customer_id=outfit.customer_id,
        outfit_image_id=outfit_image_id,
        event_type="image_shared",
        event_source=platform or "share_button",
        metadata=event_metadata,
    )

    return analytics


def track_gap_product_conversion(
    db: Session,
    *,
    outfit_image_id: int,
    event_type: str,
    product_id: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> Optional[SeasonalCampaignAnalytics]:
    """
    Track gap product conversion funnel:
    - gap_product_viewed
    - gap_product_added_to_cart
    - gap_product_purchased
    """
    outfit = db.get(GeneratedOutfitImage, outfit_image_id)
    if not outfit:
        return None

    analytics = db.scalar(
        select(SeasonalCampaignAnalytics).where(
            SeasonalCampaignAnalytics.outfit_image_id == outfit_image_id
        )
    )

    if analytics:
        now = utc_now()

        if event_type == "gap_product_viewed":
            analytics.gap_product_viewed_at = now
        elif event_type == "gap_product_added_to_cart":
            analytics.gap_product_added_to_cart_at = now
        elif event_type == "gap_product_purchased":
            analytics.gap_product_purchased_at = now

        analytics.updated_at = now
        db.flush()

    event_metadata = {"product_id": product_id} if product_id else {}
    if metadata:
        event_metadata.update(metadata)

    track_event(
        db,
        store_id=outfit.store_id,
        customer_id=outfit.customer_id,
        outfit_image_id=outfit_image_id,
        event_type=event_type,
        event_source="gap_recommendation",
        metadata=event_metadata,
    )

    return analytics


def calculate_engagement_score(analytics: SeasonalCampaignAnalytics) -> float:
    """
    Calculate engagement score based on tracked events.

    Scoring:
    - Email opened: 10 points
    - Email clicked: 15 points
    - Image saved: 20 points
    - Image shared: 25 points
    - Gap link clicked: 15 points
    - Gap product viewed: 10 points
    - Gap product added to cart: 20 points
    - Gap product purchased: 50 points
    """
    score = 0.0

    if analytics.email_opened_at:
        score += 10
    if analytics.email_clicked_at:
        score += 15
    if analytics.image_saved_count and analytics.image_saved_count > 0:
        score += min(20 * analytics.image_saved_count, 40)
    if analytics.image_shared_count and analytics.image_shared_count > 0:
        score += min(25 * analytics.image_shared_count, 50)
    if analytics.gap_link_clicked_at:
        score += 15
    if analytics.gap_product_viewed_at:
        score += 10
    if analytics.gap_product_added_to_cart_at:
        score += 20
    if analytics.gap_product_purchased_at:
        score += 50

    return min(score, 200.0)


def track_event(
    db: Session,
    *,
    store_id: int,
    customer_id: Optional[int],
    outfit_image_id: Optional[int],
    event_type: str,
    event_source: Optional[str] = None,
    url: Optional[str] = None,
    referrer: Optional[str] = None,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> CampaignEngagementEvent:
    """Create an individual engagement event record."""
    event = CampaignEngagementEvent(
        store_id=store_id,
        customer_id=customer_id,
        outfit_image_id=outfit_image_id,
        event_type=event_type,
        event_source=event_source,
        url=url,
        referrer=referrer,
        user_agent=user_agent,
        ip_address=ip_address,
        metadata_json=metadata or {},
    )

    db.add(event)
    db.flush()

    return event


def get_campaign_metrics(
    db: Session,
    store_id: int,
    season: Optional[str] = None,
    year: Optional[int] = None,
) -> dict[str, Any]:
    """
    Get aggregate metrics for a campaign.

    Returns:
        - Total sent
        - Open rate
        - Click rate
        - Save rate
        - Share rate
        - Gap link click rate
        - Gap conversion rate
        - Average engagement score
    """
    query = select(SeasonalCampaignAnalytics).where(
        SeasonalCampaignAnalytics.store_id == store_id
    )

    if season:
        query = query.where(SeasonalCampaignAnalytics.season == season)
    if year:
        query = query.where(SeasonalCampaignAnalytics.year == year)

    results = db.scalars(query).all()

    if not results:
        return {
            "total_sent": 0,
            "total_opened": 0,
            "total_clicked": 0,
            "total_saved": 0,
            "total_shared": 0,
            "gap_link_clicked": 0,
            "gap_converted": 0,
            "open_rate": 0.0,
            "click_rate": 0.0,
            "save_rate": 0.0,
            "share_rate": 0.0,
            "gap_click_rate": 0.0,
            "gap_conversion_rate": 0.0,
            "avg_engagement_score": 0.0,
        }

    total = len(results)
    opened = sum(1 for r in results if r.email_opened_at)
    clicked = sum(1 for r in results if r.email_clicked_at)
    saved = sum(1 for r in results if r.image_saved_count and r.image_saved_count > 0)
    shared = sum(
        1 for r in results if r.image_shared_count and r.image_shared_count > 0
    )
    gap_clicked = sum(1 for r in results if r.gap_link_clicked_at)
    gap_converted = sum(1 for r in results if r.gap_product_purchased_at)

    for r in results:
        r.engagement_score = calculate_engagement_score(r)
    db.flush()

    avg_score = (
        sum(r.engagement_score or 0 for r in results) / total if total > 0 else 0
    )

    return {
        "total_sent": total,
        "total_opened": opened,
        "total_clicked": clicked,
        "total_saved": saved,
        "total_shared": shared,
        "gap_link_clicked": gap_clicked,
        "gap_converted": gap_converted,
        "open_rate": round(opened / total, 4) if total > 0 else 0.0,
        "click_rate": round(clicked / total, 4) if total > 0 else 0.0,
        "save_rate": round(saved / total, 4) if total > 0 else 0.0,
        "share_rate": round(shared / total, 4) if total > 0 else 0.0,
        "gap_click_rate": round(gap_clicked / total, 4) if total > 0 else 0.0,
        "gap_conversion_rate": round(gap_converted / total, 4) if total > 0 else 0.0,
        "avg_engagement_score": round(avg_score, 2),
    }
