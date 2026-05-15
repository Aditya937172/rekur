from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GeneratedMessage(Base):
    __tablename__ = "generated_messages"
    __table_args__ = (
        Index("ix_generated_messages_store_id", "store_id"),
        Index("ix_generated_messages_customer_id", "customer_id"),
        Index("ix_generated_messages_status", "status"),
        Index("ix_generated_messages_store_status", "store_id", "status"),
        Index(
            "ix_generated_messages_store_customer_product_status",
            "store_id",
            "customer_id",
            "product_id",
            "status",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    channel = Column(String(32), default="email", nullable=False)
    intent = Column(String(32), nullable=False)
    score = Column(Integer, default=0, nullable=False)
    product_title = Column(String(500), nullable=True)
    recommendation_reason = Column(Text, nullable=True)
    message = Column(Text, nullable=False)
    status = Column(String(32), default="draft", nullable=False)
    provider = Column(String(64), default="groq", nullable=False)
    model_name = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
