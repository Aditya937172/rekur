from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas import DeliveredOrderCreateRequest, DeliveredOrderPipelineResponse
from app.services.order_delivery_service import (
    OrderDeliveryServiceError,
    create_delivered_order_and_trigger_pipeline,
)


router = APIRouter(prefix="/stores/{store_id}/orders", tags=["orders"])


@router.post("/delivered", response_model=DeliveredOrderPipelineResponse)
async def create_delivered_order(
    store_id: int,
    request: DeliveredOrderCreateRequest,
    db: Session = Depends(get_db),
) -> DeliveredOrderPipelineResponse:
    try:
        return await run_in_threadpool(
            create_delivered_order_and_trigger_pipeline,
            db,
            store_id,
            request,
        )
    except OrderDeliveryServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
