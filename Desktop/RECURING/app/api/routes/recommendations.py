from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.auth import require_store_access
from app.db.session import get_db
from app.schemas import CustomerRecommendations
from app.services.recommendation_engine import (
    RecommendationEngineError,
    get_recommendations_for_customer,
    get_recommendations_for_customers,
)


router = APIRouter(
    prefix="/stores/{store_id}/recommendations",
    tags=["recommendations"],
    dependencies=[Depends(require_store_access)],
)


@router.get("/customers", response_model=list[CustomerRecommendations])
async def list_customer_recommendations(
    store_id: int,
    customer_limit: int = Query(default=5000, ge=1, le=10000),
    product_limit: int = Query(default=5, ge=1, le=10),
    db: Session = Depends(get_db),
) -> list[CustomerRecommendations]:
    try:
        return await run_in_threadpool(
            get_recommendations_for_customers,
            db,
            store_id,
            customer_limit=customer_limit,
            product_limit=product_limit,
        )
    except RecommendationEngineError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get("/{customer_id}", response_model=CustomerRecommendations)
async def get_customer_recommendations(
    store_id: int,
    customer_id: int,
    product_limit: int = Query(default=5, ge=1, le=10),
    db: Session = Depends(get_db),
) -> CustomerRecommendations:
    try:
        return await run_in_threadpool(
            get_recommendations_for_customer,
            db,
            store_id,
            customer_id,
            product_limit=product_limit,
        )
    except RecommendationEngineError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
