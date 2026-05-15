from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Index, Integer, String, Text

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GeneratedOutfitImage(Base):
    __tablename__ = "generated_outfit_images"
    __table_args__ = (
        Index("ix_generated_outfit_images_store_id", "store_id"),
        Index("ix_generated_outfit_images_customer_id", "customer_id"),
        Index("ix_generated_outfit_images_order_id", "order_id"),
        Index("ix_generated_outfit_images_status", "status"),
        Index("ix_generated_outfit_images_store_status", "store_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    buyer_memory_id = Column(Integer, ForeignKey("buyer_memory.id"), nullable=True)
    trigger_reason = Column(String(128), default="order_delivered_followup", nullable=False)
    status = Column(String(32), default="generated", nullable=False)
    provider = Column(String(64), default="image_api", nullable=False)
    model_name = Column(String(128), nullable=True)
    task_id = Column(String(255), nullable=True, index=True)
    task_status = Column(String(64), nullable=True)
    task_progress = Column(Integer, nullable=True)
    prompt = Column(Text, nullable=False)
    image_url = Column(Text, nullable=True)
    image_base64 = Column(Text, nullable=True)
    recommended_products_json = Column(JSON, nullable=False, default=list)
    reference_image_urls_json = Column(JSON, nullable=False, default=list)
    email_subject = Column(String(255), nullable=True)
    email_body = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    sent_at = Column(DateTime(timezone=True), nullable=True)
