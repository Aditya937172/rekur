from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AppUser(Base):
    __tablename__ = "app_users"
    __table_args__ = (
        Index("ix_app_users_email", "email", unique=True),
        Index("ix_app_users_external_id", "external_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(320), nullable=False)
    name = Column(String(255), nullable=True)
    external_id = Column(String(255), nullable=True)
    auth_provider = Column(String(64), default="local", nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    store_ownerships = relationship(
        "StoreOwnership",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class StoreOwnership(Base):
    __tablename__ = "store_ownerships"
    __table_args__ = (
        UniqueConstraint("user_id", "store_id", name="uq_store_ownership_user_store"),
        Index("ix_store_ownerships_user_id", "user_id"),
        Index("ix_store_ownerships_store_id", "store_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("app_users.id"), nullable=False)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    role = Column(String(64), default="owner", nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    user = relationship("AppUser", back_populates="store_ownerships")
    store = relationship("Store", back_populates="ownerships")
