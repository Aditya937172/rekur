from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, Event, Product, Store, TrackingSession
from app.schemas import EventCreate


NEW_VISIT_AFTER = timedelta(minutes=30)


class EventTrackingError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_timestamp(value: datetime | None) -> datetime:
    if value is None:
        return utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def record_event(db: Session, payload: EventCreate) -> None:
    event_time = normalize_timestamp(payload.timestamp)
    store = resolve_store(db, payload.store_id)
    customer = resolve_customer(db, store.id, payload.customer_id)
    product = resolve_product(db, store.id, payload.product_id)
    tracking_session = get_or_create_session(
        db=db,
        store=store,
        session_id=payload.session_id,
        customer=customer,
        event_time=event_time,
    )

    metadata = dict(payload.metadata or {})
    if payload.time_since_last_visit is not None:
        metadata["time_since_last_visit"] = payload.time_since_last_visit
    if payload.is_first_time is not None:
        metadata["client_is_first_time"] = payload.is_first_time

    event = Event(
        store_id=store.id,
        session_id=tracking_session.id,
        customer_id=customer.id if customer else None,
        event_type=payload.event_type,
        product_id=product.id if product else None,
        page_url=payload.page_url,
        referrer=payload.referrer,
        device_type=payload.device_type,
        time_spent=payload.time_spent,
        timestamp=event_time,
        metadata_json=metadata,
    )
    db.add(event)
    db.commit()


def resolve_store(db: Session, store_id: int | None) -> Store:
    if store_id is not None:
        store = db.get(Store, store_id)
        if not store:
            raise EventTrackingError(f"Store {store_id} was not found.")
        return store

    stores = db.scalars(select(Store).limit(2)).all()
    if len(stores) == 1:
        return stores[0]
    if not stores:
        raise EventTrackingError("No store exists. Create or sync a store first.")
    raise EventTrackingError("store_id is required when more than one store exists.")


def resolve_customer(
    db: Session,
    store_id: int,
    customer_id: int | str | None,
) -> Customer | None:
    if customer_id in (None, ""):
        return None
    raw = str(customer_id).strip()

    if raw.isdigit():
        local_customer = db.get(Customer, int(raw))
        if local_customer and local_customer.store_id == store_id:
            return local_customer

    customer = db.scalar(
        select(Customer).where(
            Customer.store_id == store_id,
            Customer.shopify_customer_id == raw,
        )
    )
    if customer:
        return customer

    if "@" in raw:
        return db.scalar(
            select(Customer).where(
                Customer.store_id == store_id,
                Customer.email == raw,
            )
        )
    return None


def resolve_product(
    db: Session,
    store_id: int,
    product_id: int | str | None,
) -> Product | None:
    if product_id in (None, ""):
        return None
    raw = str(product_id).strip()

    product = db.scalar(
        select(Product).where(
            Product.store_id == store_id,
            Product.shopify_product_id == raw,
        )
    )
    if product:
        return product

    if raw.isdigit():
        local_product = db.get(Product, int(raw))
        if local_product and local_product.store_id == store_id:
            return local_product
    return None


def get_or_create_session(
    *,
    db: Session,
    store: Store,
    session_id: str,
    customer: Customer | None,
    event_time: datetime,
) -> TrackingSession:
    tracking_session = db.scalar(
        select(TrackingSession).where(TrackingSession.session_id == session_id)
    )
    if not tracking_session:
        tracking_session = TrackingSession(
            store_id=store.id,
            session_id=session_id,
            customer_id=customer.id if customer else None,
            is_first_time=True,
            visit_count=1,
            started_at=event_time,
            last_seen_at=event_time,
            created_at=event_time,
        )
        db.add(tracking_session)
        db.flush()
        return tracking_session

    if tracking_session.store_id != store.id:
        raise EventTrackingError("session_id belongs to a different store.")

    last_seen = normalize_db_datetime(tracking_session.last_seen_at)
    if event_time - last_seen > NEW_VISIT_AFTER:
        tracking_session.visit_count += 1

    tracking_session.last_seen_at = max(event_time, last_seen)
    if customer:
        tracking_session.customer_id = customer.id
    db.flush()
    return tracking_session


def normalize_db_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return utc_now()
