from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Customer, Event, Order, Store, TrackingSession
from app.schemas import CustomerIntent, IntentSignals


IntentFilter = Literal["high", "medium", "low"]


class IntentEngineError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_customer_intents(
    db: Session,
    store_id: int,
    *,
    intent_filter: IntentFilter | None = None,
    limit: int = 250,
) -> list[CustomerIntent]:
    if not db.get(Store, store_id):
        raise IntentEngineError(f"Store {store_id} was not found.")

    now = utc_now()
    cutoff_3d = now - timedelta(days=3)
    cutoff_7d = now - timedelta(days=7)

    event_customer_id = func.coalesce(
        Event.customer_id,
        TrackingSession.customer_id,
    )

    event_agg = (
        select(
            event_customer_id.label("customer_id"),
            func.sum(case((Event.event_type == "product_view", 1), else_=0)).label(
                "product_views_count"
            ),
            func.coalesce(func.sum(Event.time_spent), 0).label("total_time_spent"),
            func.sum(case((Event.event_type == "add_to_cart", 1), else_=0)).label(
                "added_to_cart_count"
            ),
            func.max(Event.timestamp).label("last_event_at"),
        )
        .join(TrackingSession, TrackingSession.id == Event.session_id)
        .where(
            Event.store_id == store_id,
            Event.timestamp >= cutoff_3d,
            event_customer_id.is_not(None),
        )
        .group_by(event_customer_id)
        .subquery()
    )

    session_agg = (
        select(
            TrackingSession.customer_id.label("customer_id"),
            func.count(TrackingSession.id).label("session_count"),
            func.max(TrackingSession.last_seen_at).label("last_session_at"),
        )
        .where(
            TrackingSession.store_id == store_id,
            TrackingSession.customer_id.is_not(None),
            TrackingSession.last_seen_at >= cutoff_3d,
        )
        .group_by(TrackingSession.customer_id)
        .subquery()
    )

    order_agg = (
        select(
            Order.customer_id.label("customer_id"),
            func.max(Order.created_at).label("last_order_date"),
        )
        .where(Order.store_id == store_id, Order.customer_id.is_not(None))
        .group_by(Order.customer_id)
        .subquery()
    )

    active_event_customers = (
        select(event_customer_id.label("customer_id"))
        .join(TrackingSession, TrackingSession.id == Event.session_id)
        .where(
            Event.store_id == store_id,
            Event.timestamp >= cutoff_7d,
            event_customer_id.is_not(None),
        )
        .group_by(event_customer_id)
    )
    active_session_customers = (
        select(TrackingSession.customer_id.label("customer_id"))
        .where(
            TrackingSession.store_id == store_id,
            TrackingSession.customer_id.is_not(None),
            TrackingSession.last_seen_at >= cutoff_7d,
        )
        .group_by(TrackingSession.customer_id)
    )
    active_customers = active_event_customers.union(active_session_customers).subquery()

    rows = db.execute(
        select(
            Customer.id,
            Customer.first_name,
            Customer.last_name,
            Customer.email,
            func.coalesce(event_agg.c.product_views_count, 0),
            func.coalesce(event_agg.c.total_time_spent, 0),
            func.coalesce(event_agg.c.added_to_cart_count, 0),
            func.coalesce(session_agg.c.session_count, 0),
            event_agg.c.last_event_at,
            session_agg.c.last_session_at,
            order_agg.c.last_order_date,
        )
        .join(active_customers, active_customers.c.customer_id == Customer.id)
        .outerjoin(event_agg, event_agg.c.customer_id == Customer.id)
        .outerjoin(session_agg, session_agg.c.customer_id == Customer.id)
        .outerjoin(order_agg, order_agg.c.customer_id == Customer.id)
        .where(Customer.store_id == store_id)
        .limit(limit)
    ).all()

    scored = [
        build_customer_intent(
            now=now,
            customer_id=row[0],
            first_name=row[1],
            last_name=row[2],
            email=row[3],
            product_views_count=int(row[4] or 0),
            total_time_spent=int(row[5] or 0),
            added_to_cart_count=int(row[6] or 0),
            session_count=int(row[7] or 0),
            last_visit_at=max_datetime(row[8], row[9]),
            last_order_date=normalize_datetime(row[10]),
        )
        for row in rows
    ]

    if intent_filter:
        scored = [item for item in scored if item.intent == intent_filter]

    return sorted(scored, key=lambda item: item.score, reverse=True)


def build_customer_intent(
    *,
    now: datetime,
    customer_id: int,
    first_name: str | None,
    last_name: str | None,
    email: str | None,
    product_views_count: int,
    total_time_spent: int,
    added_to_cart_count: int,
    session_count: int,
    last_visit_at: datetime | None,
    last_order_date: datetime | None,
) -> CustomerIntent:
    days_since_last_visit = days_since(now, last_visit_at)
    days_since_last_order = days_since(now, last_order_date)
    score = calculate_score(
        product_views_count=product_views_count,
        total_time_spent=total_time_spent,
        session_count=session_count,
        added_to_cart_count=added_to_cart_count,
        days_since_last_visit=days_since_last_visit,
        days_since_last_order=days_since_last_order,
    )
    intent = classify_intent(score)
    signals = IntentSignals(
        product_views=product_views_count,
        sessions=session_count,
        time_spent=total_time_spent,
        added_to_cart=added_to_cart_count,
        days_since_last_visit=days_since_last_visit,
        days_since_last_order=days_since_last_order,
    )
    return CustomerIntent(
        customer_id=customer_id,
        name=customer_name(first_name, last_name, email),
        email=email,
        intent=intent,
        score=score,
        signals=signals,
        reason=build_reason(signals),
        last_visit_at=last_visit_at,
        last_order_date=last_order_date,
    )


def calculate_score(
    *,
    product_views_count: int,
    total_time_spent: int,
    session_count: int,
    added_to_cart_count: int,
    days_since_last_visit: int | None,
    days_since_last_order: int | None,
) -> int:
    score = 0
    if product_views_count >= 3:
        score += 30
    if total_time_spent > 60000:
        score += 20
    if session_count >= 2:
        score += 20
    if added_to_cart_count >= 1:
        score += 40
    if days_since_last_visit is not None and days_since_last_visit <= 1:
        score += 30
    if days_since_last_order is not None and days_since_last_order <= 30:
        score += 10
    return score


def classify_intent(score: int) -> IntentFilter:
    if score >= 80:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def build_reason(signals: IntentSignals) -> str:
    reasons: list[str] = []
    if signals.added_to_cart >= 1:
        reasons.append("added to cart")
    if signals.product_views >= 3:
        reasons.append("viewed products multiple times")
    if signals.sessions >= 2:
        reasons.append("returned across multiple sessions")
    if signals.time_spent > 60000:
        reasons.append("spent more than 60 seconds browsing")
    if signals.days_since_last_visit is not None and signals.days_since_last_visit <= 1:
        reasons.append("visited within the last 24 hours")
    if signals.days_since_last_order is not None and signals.days_since_last_order <= 30:
        reasons.append("ordered within the last 30 days")
    if not reasons:
        return "Limited recent buying signals"
    return sentence_case(", ".join(reasons))


def customer_name(
    first_name: str | None,
    last_name: str | None,
    email: str | None,
) -> str:
    name = " ".join(part for part in [first_name, last_name] if part).strip()
    return name or email or "Unknown customer"


def days_since(now: datetime, value: datetime | None) -> int | None:
    if value is None:
        return None
    normalized = normalize_datetime(value)
    if normalized is None:
        return None
    delta = now - normalized
    if delta.total_seconds() < 0:
        return 0
    return int(delta.total_seconds() // 86400)


def normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def max_datetime(left: datetime | None, right: datetime | None) -> datetime | None:
    left_normalized = normalize_datetime(left)
    right_normalized = normalize_datetime(right)
    if left_normalized and right_normalized:
        return max(left_normalized, right_normalized)
    return left_normalized or right_normalized


def sentence_case(value: str) -> str:
    if not value:
        return value
    return value[0].upper() + value[1:]
