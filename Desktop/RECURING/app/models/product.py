from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("store_id", "shopify_product_id", name="uq_products_store_shopify_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    shopify_product_id = Column(String(64), nullable=False, index=True)
    handle = Column(String(500), nullable=True, index=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Numeric(12, 2), nullable=True)
    image_url = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)
    in_stock = Column(Boolean, default=True, nullable=False)
    variant_inventory_json = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    store = relationship("Store", back_populates="products")
    order_items = relationship("OrderItem", back_populates="product")
    events = relationship("Event", back_populates="product")
