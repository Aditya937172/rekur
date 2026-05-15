from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EmailEngagement(Base):
    __tablename__ = "email_engagement"
    __table_args__ = (
        Index("ix_email_engagement_store_customer", "store_id", "customer_id"),
        Index("ix_email_engagement_store_event_time", "store_id", "event_type", "timestamp"),
        Index("ix_email_engagement_campaign", "campaign_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    send_log_id = Column(Integer, ForeignKey("retention_send_logs.id"), nullable=True, index=True)
    provider_message_id = Column(String(255), nullable=True, index=True)
    campaign_type = Column(String(128), nullable=True, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    url = Column(Text, nullable=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    timestamp = Column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)


class ReturnRefund(Base):
    __tablename__ = "return_refunds"
    __table_args__ = (
        Index("ix_return_refunds_store_customer", "store_id", "customer_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, index=True)
    shopify_refund_id = Column(String(64), nullable=True, index=True)
    status = Column(String(64), nullable=False, default="recorded", index=True)
    amount = Column(Numeric(12, 2), nullable=False, default=0)
    reason = Column(Text, nullable=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)


class CustomerProfile(Base):
    __tablename__ = "customer_profiles"
    __table_args__ = (
        UniqueConstraint("store_id", "customer_id", name="uq_customer_profiles_store_customer"),
        Index("ix_customer_profiles_store_id", "store_id"),
        Index("ix_customer_profiles_customer_id", "customer_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    dominant_aesthetic = Column(String(255), nullable=True)
    color_palette = Column(Text, nullable=True)
    preference_dimensions_json = Column(JSON, nullable=False, default=dict)
    conversation_history_json = Column(JSON, nullable=False, default=list)
    last_reply_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class OutfitImageCache(Base):
    __tablename__ = "outfit_image_cache"
    __table_args__ = (
        Index("ix_outfit_image_cache_store_trigger", "store_id", "trigger_reason"),
        Index("ix_outfit_image_cache_updated_at", "updated_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    trigger_reason = Column(String(128), nullable=False, index=True)
    cache_key = Column(String(500), nullable=False, index=True)
    product_ids_json = Column(JSON, nullable=False, default=list)
    embedding_json = Column(JSON, nullable=False, default=list)
    image_url = Column(Text, nullable=True)
    image_base64 = Column(Text, nullable=True)
    provider = Column(String(64), nullable=True)
    model_name = Column(String(128), nullable=True)
    hit_count = Column(Integer, default=0, nullable=False)
    avg_engagement_score = Column(Float, default=0.0, nullable=False)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class RetentionSendLog(Base):
    __tablename__ = "retention_send_logs"
    __table_args__ = (
        Index("ix_retention_send_logs_store_customer", "store_id", "customer_id"),
        Index("ix_retention_send_logs_campaign", "campaign_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    campaign_type = Column(String(128), nullable=False, index=True)
    trigger_reason = Column(String(128), nullable=True)
    channel = Column(String(64), nullable=False, default="email")
    status = Column(String(64), nullable=False, default="sent", index=True)
    subject = Column(String(500), nullable=True)
    provider = Column(String(64), nullable=True)
    provider_message_id = Column(String(255), nullable=True, index=True)
    outfit_image_id = Column(Integer, ForeignKey("generated_outfit_images.id"), nullable=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    sent_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class RetentionCampaignState(Base):
    __tablename__ = "retention_campaign_states"
    __table_args__ = (
        UniqueConstraint(
            "store_id",
            "customer_id",
            "campaign_type",
            name="uq_campaign_state_store_customer_campaign",
        ),
        Index("ix_campaign_states_store_campaign", "store_id", "campaign_type"),
        Index("ix_campaign_states_status", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    campaign_type = Column(String(128), nullable=False, index=True)
    stage = Column(String(64), nullable=True)
    status = Column(String(64), nullable=False, default="active", index=True)
    score = Column(Float, default=0.0, nullable=False)
    last_action_at = Column(DateTime(timezone=True), nullable=True)
    next_action_at = Column(DateTime(timezone=True), nullable=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class CustomerReply(Base):
    __tablename__ = "customer_replies"
    __table_args__ = (
        Index("ix_customer_replies_store_customer", "store_id", "customer_id"),
        Index("ix_customer_replies_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    send_log_id = Column(Integer, ForeignKey("retention_send_logs.id"), nullable=True)
    inbound_text = Column(Text, nullable=False)
    extracted_preferences_json = Column(JSON, nullable=False, default=dict)
    response_text = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
