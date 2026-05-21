from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.auth import require_outfit_access, require_store_access
from app.db.session import get_db
from app.schemas import (
    FirstOrderAnniversaryCampaignRequest,
    FirstOrderAnniversaryCampaignResponse,
    GenerateOutfitImageRequest,
    GeneratedOutfitImageResponse,
    OutfitEmailSendResponse,
    SendOutfitEmailRequest,
)
from app.services.anniversary_service import (
    AnniversaryServiceError,
    run_first_order_anniversary_campaign,
)
from app.services.outfit_service import (
    OutfitServiceError,
    generate_outfit_for_customer,
    list_outfits,
    send_outfit_email,
)


router = APIRouter(tags=["outfits"])
store_router = APIRouter(
    prefix="/stores/{store_id}/outfits",
    dependencies=[Depends(require_store_access)],
)
outfit_router = APIRouter(prefix="/outfits")


@store_router.post("/generate", response_model=GeneratedOutfitImageResponse)
async def generate_outfit_image(
    store_id: int,
    request: GenerateOutfitImageRequest,
    db: Session = Depends(get_db),
) -> GeneratedOutfitImageResponse:
    try:
        return await run_in_threadpool(
            generate_outfit_for_customer,
            db,
            store_id,
            request,
        )
    except OutfitServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@store_router.post(
    "/anniversary",
    response_model=FirstOrderAnniversaryCampaignResponse,
)
async def run_first_purchase_anniversary_campaign(
    store_id: int,
    request: FirstOrderAnniversaryCampaignRequest | None = None,
    db: Session = Depends(get_db),
) -> FirstOrderAnniversaryCampaignResponse:
    try:
        return await run_in_threadpool(
            run_first_order_anniversary_campaign,
            db,
            store_id,
            request or FirstOrderAnniversaryCampaignRequest(),
        )
    except AnniversaryServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@store_router.get("", response_model=list[GeneratedOutfitImageResponse])
async def list_store_outfits(
    store_id: int,
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[GeneratedOutfitImageResponse]:
    try:
        return await run_in_threadpool(list_outfits, db, store_id, status=status)
    except OutfitServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@outfit_router.post("/{outfit_id}/send", response_model=OutfitEmailSendResponse)
async def send_outfit_followup_email(
    outfit_id: int,
    request: SendOutfitEmailRequest | None = None,
    _outfit=Depends(require_outfit_access),
    db: Session = Depends(get_db),
) -> OutfitEmailSendResponse:
    try:
        return await run_in_threadpool(
            send_outfit_email,
            db,
            outfit_id,
            request,
        )
    except OutfitServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


router.include_router(store_router)
router.include_router(outfit_router)
