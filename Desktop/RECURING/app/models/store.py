from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.orm import relationship

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Store(Base):
    __tablename__ = "stores"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    nango_connection_id = Column(String(255), nullable=False, unique=True, index=True)
    shopify_store_domain = Column(String(255), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    tracking_installed = Column(Boolean, default=False, nullable=False)
    tracking_installed_at = Column(DateTime(timezone=True), nullable=True)

    products = relationship("Product", back_populates="store", cascade="all, delete-orphan")
    customers = relationship("Customer", back_populates="store", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="store", cascade="all, delete-orphan")
    sync_runs = relationship("SyncRun", back_populates="store", cascade="all, delete-orphan")
    sessions = relationship("TrackingSession", back_populates="store", cascade="all, delete-orphan")
    events = relationship("Event", back_populates="store", cascade="all, delete-orphan")
    ownerships = relationship(
        "StoreOwnership",
        back_populates="store",
        cascade="all, delete-orphan",
    )
