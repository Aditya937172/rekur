from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="running", index=True)
    started_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    products_synced = Column(Integer, default=0, nullable=False)
    customers_synced = Column(Integer, default=0, nullable=False)
    orders_synced = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)

    store = relationship("Store", back_populates="sync_runs")
