from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.auth import require_store_access
from app.db.session import get_db
from app.schemas import (
    CampaignRunRequest,
    CampaignRunResponse,
    ChurnRiskResponse,
    CustomerReplyCreate,
    CustomerReplyResponse,
    EmailEngagementCreate,
    EmailEngagementResponse,
    ReturnRefundCreate,
    ReturnRefundResponse,
    SilentCustomerEngagementSeedRequest,
    SilentCustomerEngagementSeedResponse,
    SilentCustomerResponse,
)
from app.services.retention_campaign_service import (
    RetentionCampaignServiceError,
    compute_churn_risk,
    detect_silent_customers,
    run_pre_churn_campaign,
    run_seasonal_lookbook_campaign,
    run_silent_customer_campaign,
)
from app.services.retention_data_service import (
    RetentionDataServiceError,
    handle_customer_reply,
    record_email_engagement,
    record_return_refund,
    seed_silent_customer_engagement,
)


router = APIRouter(
    prefix="/stores/{store_id}/retention",
    tags=["retention"],
    dependencies=[Depends(require_store_access)],
)


@router.post("/email-engagement", response_model=EmailEngagementResponse)
async def create_email_engagement(
    store_id: int,
    request: EmailEngagementCreate,
    db: Session = Depends(get_db),
) -> EmailEngagementResponse:
    try:
        return await run_in_threadpool(record_email_engagement, db, store_id, request)
    except RetentionDataServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post(
    "/email-engagement/seed-silent-customer",
    response_model=SilentCustomerEngagementSeedResponse,
)
async def seed_silent_customer_email_engagement(
    store_id: int,
    request: SilentCustomerEngagementSeedRequest,
    db: Session = Depends(get_db),
) -> SilentCustomerEngagementSeedResponse:
    try:
        return await run_in_threadpool(
            seed_silent_customer_engagement,
            db,
            store_id,
            request,
        )
    except RetentionDataServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/returns-refunds", response_model=ReturnRefundResponse)
async def create_return_refund(
    store_id: int,
    request: ReturnRefundCreate,
    db: Session = Depends(get_db),
) -> ReturnRefundResponse:
    try:
        return await run_in_threadpool(record_return_refund, db, store_id, request)
    except RetentionDataServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/replies", response_model=CustomerReplyResponse)
async def create_customer_reply(
    store_id: int,
    request: CustomerReplyCreate,
    db: Session = Depends(get_db),
) -> CustomerReplyResponse:
    try:
        return await run_in_threadpool(handle_customer_reply, db, store_id, request)
    except RetentionDataServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/churn-risk", response_model=list[ChurnRiskResponse])
async def list_churn_risk(
    store_id: int,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[ChurnRiskResponse]:
    try:
        return await run_in_threadpool(compute_churn_risk, db, store_id, limit=limit)
    except RetentionCampaignServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/pre-churn/run", response_model=CampaignRunResponse)
async def run_pre_churn(
    store_id: int,
    request: CampaignRunRequest | None = None,
    db: Session = Depends(get_db),
) -> CampaignRunResponse:
    try:
        return await run_in_threadpool(
            run_pre_churn_campaign,
            db,
            store_id,
            request or CampaignRunRequest(),
        )
    except RetentionCampaignServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/silent-customers", response_model=list[SilentCustomerResponse])
async def list_silent_customers(
    store_id: int,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[SilentCustomerResponse]:
    try:
        return await run_in_threadpool(detect_silent_customers, db, store_id, limit=limit)
    except RetentionCampaignServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/silent-customers/run", response_model=CampaignRunResponse)
async def run_silent_customers(
    store_id: int,
    request: CampaignRunRequest | None = None,
    db: Session = Depends(get_db),
) -> CampaignRunResponse:
    try:
        return await run_in_threadpool(
            run_silent_customer_campaign,
            db,
            store_id,
            request or CampaignRunRequest(),
        )
    except RetentionCampaignServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/seasonal-lookbook/run", response_model=CampaignRunResponse)
async def run_seasonal_lookbook(
    store_id: int,
    request: CampaignRunRequest | None = None,
    season: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> CampaignRunResponse:
    try:
        return await run_in_threadpool(
            run_seasonal_lookbook_campaign,
            db,
            store_id,
            request or CampaignRunRequest(),
            season=season,
        )
    except RetentionCampaignServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
