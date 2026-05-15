from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas import CustomerIntent
from app.services.intent_engine import IntentEngineError, get_customer_intents


router = APIRouter(prefix="/stores/{store_id}/intent", tags=["intent"])


@router.get("/customers", response_model=list[CustomerIntent])
async def list_customer_intents(
    store_id: int,
    limit: int = Query(default=250, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[CustomerIntent]:
    try:
        return await run_in_threadpool(
            get_customer_intents,
            db,
            store_id,
            limit=limit,
        )
    except IntentEngineError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get("/high", response_model=list[CustomerIntent])
async def list_high_intent_customers(
    store_id: int,
    limit: int = Query(default=250, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[CustomerIntent]:
    try:
        return await run_in_threadpool(
            get_customer_intents,
            db,
            store_id,
            intent_filter="high",
            limit=limit,
        )
    except IntentEngineError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
