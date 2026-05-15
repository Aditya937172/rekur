from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TrackingSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_sessions_session_id"),
        Index("ix_sessions_store_last_seen", "store_id", "last_seen_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    session_id = Column(String(255), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    is_first_time = Column(Boolean, default=True, nullable=False)
    visit_count = Column(Integer, default=1, nullable=False)
    started_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    last_seen_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    store = relationship("Store", back_populates="sessions")
    customer = relationship("Customer", back_populates="sessions")
    events = relationship("Event", back_populates="session", cascade="all, delete-orphan")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_store_timestamp", "store_id", "timestamp"),
        Index("ix_events_store_type", "store_id", "event_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True, index=True)
    page_url = Column(Text, nullable=True)
    referrer = Column(Text, nullable=True)
    device_type = Column(String(32), nullable=True)
    time_spent = Column(Integer, nullable=True)
    timestamp = Column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)

    store = relationship("Store", back_populates="events")
    session = relationship("TrackingSession", back_populates="events")
    customer = relationship("Customer", back_populates="events")
    product = relationship("Product", back_populates="events")
