from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint(
            "store_id", "shopify_customer_id", name="uq_customers_store_shopify_id"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    shopify_customer_id = Column(String(64), nullable=False, index=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    email = Column(String(320), nullable=True, index=True)
    phone = Column(String(64), nullable=True)
    city = Column(String(255), nullable=True)
    country = Column(String(255), nullable=True)
    gender = Column(String(32), nullable=True)
    total_orders = Column(Integer, default=0, nullable=False)
    total_spent = Column(Numeric(12, 2), default=0, nullable=False)
    last_order_date = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    store = relationship("Store", back_populates="customers")
    orders = relationship("Order", back_populates="customer")
    sessions = relationship("TrackingSession", back_populates="customer")
    events = relationship("Event", back_populates="customer")
