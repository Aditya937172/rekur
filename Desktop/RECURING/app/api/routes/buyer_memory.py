from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas import BuyerMemoryRebuildResponse, BuyerMemoryResponse
from app.services.buyer_memory_service import (
    BuyerMemoryServiceError,
    get_buyer_memory,
    rebuild_buyer_memory_for_store,
)


router = APIRouter(prefix="/stores/{store_id}/buyer-memory", tags=["buyer-memory"])


@router.post("/rebuild", response_model=BuyerMemoryRebuildResponse)
async def rebuild_store_buyer_memory(
    store_id: int,
    db: Session = Depends(get_db),
) -> BuyerMemoryRebuildResponse:
    try:
        count = await run_in_threadpool(rebuild_buyer_memory_for_store, db, store_id)
        db.commit()
    except BuyerMemoryServiceError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return BuyerMemoryRebuildResponse(store_id=store_id, memories_updated=count)


@router.get("/{customer_id}", response_model=BuyerMemoryResponse)
async def get_customer_buyer_memory(
    store_id: int,
    customer_id: int,
    db: Session = Depends(get_db),
) -> BuyerMemoryResponse:
    try:
        memory = await run_in_threadpool(get_buyer_memory, db, store_id, customer_id)
        db.commit()
    except BuyerMemoryServiceError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return memory
