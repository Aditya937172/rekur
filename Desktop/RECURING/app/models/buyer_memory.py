from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
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


class BuyerMemory(Base):
    __tablename__ = "buyer_memory"
    __table_args__ = (
        UniqueConstraint("store_id", "customer_id", name="uq_buyer_memory_store_customer"),
        Index("ix_buyer_memory_store_id", "store_id"),
        Index("ix_buyer_memory_customer_id", "customer_id"),
        Index("ix_buyer_memory_updated_at", "updated_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    first_order_at = Column(DateTime(timezone=True), nullable=True)
    last_order_at = Column(DateTime(timezone=True), nullable=True)
    last_order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    total_orders = Column(Integer, default=0, nullable=False)
    total_spent = Column(Numeric(12, 2), default=0, nullable=False)
    first_order_summary = Column(Text, nullable=True)
    last_order_summary = Column(Text, nullable=True)
    wardrobe_summary = Column(Text, nullable=True)
    interest_summary = Column(Text, nullable=True)
    memory_summary = Column(Text, nullable=True)
    favorite_categories = Column(Text, nullable=True)
    favorite_colors = Column(Text, nullable=True)
    style_tags = Column(Text, nullable=True)
    price_band = Column(String(64), nullable=True)
    order_history_json = Column(JSON, nullable=False, default=list)
    wardrobe_items_json = Column(JSON, nullable=False, default=list)
    recent_interests_json = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
