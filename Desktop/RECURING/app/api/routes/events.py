from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Event, Store, TrackingSession
from app.schemas import EventAck, EventCreate, EventSummary, SessionRead
from app.services.event_tracking import EventTrackingError, record_event


router = APIRouter(tags=["events"])


@router.post("/events", response_model=EventAck)
async def create_event(
    payload: EventCreate,
    db: Session = Depends(get_db),
) -> EventAck:
    try:
        await run_in_threadpool(record_event, db, payload)
    except EventTrackingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return EventAck(status="ok")


@router.get("/stores/{store_id}/events/summary", response_model=EventSummary)
async def get_events_summary(
    store_id: int,
    db: Session = Depends(get_db),
) -> EventSummary:
    return await run_in_threadpool(build_events_summary, db, store_id)


@router.get("/stores/{store_id}/sessions", response_model=list[SessionRead])
async def list_sessions(
    store_id: int,
    db: Session = Depends(get_db),
) -> list[TrackingSession]:
    return await run_in_threadpool(build_sessions_list, db, store_id)


def build_events_summary(db: Session, store_id: int) -> EventSummary:
    ensure_store_exists(db, store_id)
    total_events = db.scalar(
        select(func.count(Event.id)).where(Event.store_id == store_id)
    ) or 0
    product_views = db.scalar(
        select(func.count(Event.id)).where(
            Event.store_id == store_id,
            Event.event_type == "product_view",
        )
    ) or 0
    sessions = db.scalar(
        select(func.count(TrackingSession.id)).where(
            TrackingSession.store_id == store_id
        )
    ) or 0
    avg_time_spent = db.scalar(
        select(func.avg(case((Event.time_spent.is_not(None), Event.time_spent)))).where(
            Event.store_id == store_id
        )
    )
    return EventSummary(
        total_events=total_events,
        product_views=product_views,
        sessions=sessions,
        avg_time_spent=float(avg_time_spent or 0),
    )


def build_sessions_list(db: Session, store_id: int) -> list[TrackingSession]:
    ensure_store_exists(db, store_id)
    return list(
        db.scalars(
            select(TrackingSession)
            .where(TrackingSession.store_id == store_id)
            .order_by(TrackingSession.last_seen_at.desc())
            .limit(500)
        )
    )


def ensure_store_exists(db: Session, store_id: int) -> Store:
    store = db.get(Store, store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Store {store_id} was not found.",
        )
    return store
