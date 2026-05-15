from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas import (
    ApproveMessageResponse,
    CustomerMessage,
    GenerateMessagesRequest,
    GeneratedMessageResponse,
    RegenerateMessageRequest,
    RejectMessageResponse,
    SendApprovedMessageRequest,
    SendApprovedMessageResponse,
)
from app.services.message_engine import (
    MessageEngineError,
    generate_message_for_customer,
    generate_messages_for_customers,
)
from app.services.message_review_service import (
    MessageReviewServiceError,
    approve_message,
    generate_drafts_for_store,
    list_approved_messages,
    list_drafts,
    regenerate_message,
    reject_message,
)
from app.services.message_send_service import (
    MessageSendServiceError,
    send_approved_message,
)


router = APIRouter(tags=["messages"])
store_router = APIRouter(prefix="/stores/{store_id}/messages")
message_router = APIRouter(prefix="/messages")


@store_router.post("/generate", response_model=list[GeneratedMessageResponse])
async def generate_message_drafts(
    store_id: int,
    request: GenerateMessagesRequest | None = None,
    db: Session = Depends(get_db),
) -> list[GeneratedMessageResponse]:
    try:
        return await run_in_threadpool(
            generate_drafts_for_store,
            db,
            store_id,
            request,
        )
    except MessageReviewServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@store_router.get("/drafts", response_model=list[GeneratedMessageResponse])
async def get_message_drafts(
    store_id: int,
    db: Session = Depends(get_db),
) -> list[GeneratedMessageResponse]:
    try:
        return await run_in_threadpool(list_drafts, db, store_id)
    except MessageReviewServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@store_router.get("/approved", response_model=list[GeneratedMessageResponse])
async def get_approved_messages(
    store_id: int,
    db: Session = Depends(get_db),
) -> list[GeneratedMessageResponse]:
    try:
        return await run_in_threadpool(list_approved_messages, db, store_id)
    except MessageReviewServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@store_router.get("/customers", response_model=list[CustomerMessage])
async def list_customer_messages(
    store_id: int,
    limit: int = Query(default=5000, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> list[CustomerMessage]:
    try:
        return await run_in_threadpool(
            generate_messages_for_customers,
            db,
            store_id,
            limit=limit,
        )
    except MessageEngineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@store_router.get("/{customer_id}", response_model=CustomerMessage)
async def get_customer_message(
    store_id: int,
    customer_id: int,
    db: Session = Depends(get_db),
) -> CustomerMessage:
    try:
        return await run_in_threadpool(
            generate_message_for_customer,
            db,
            store_id,
            customer_id,
        )
    except MessageEngineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@message_router.post("/{message_id}/approve", response_model=ApproveMessageResponse)
async def approve_generated_message(
    message_id: int,
    db: Session = Depends(get_db),
) -> ApproveMessageResponse:
    try:
        return await run_in_threadpool(approve_message, db, message_id)
    except MessageReviewServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@message_router.post("/{message_id}/reject", response_model=RejectMessageResponse)
async def reject_generated_message(
    message_id: int,
    db: Session = Depends(get_db),
) -> RejectMessageResponse:
    try:
        return await run_in_threadpool(reject_message, db, message_id)
    except MessageReviewServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@message_router.post("/{message_id}/regenerate", response_model=GeneratedMessageResponse)
async def regenerate_generated_message(
    message_id: int,
    request: RegenerateMessageRequest | None = None,
    db: Session = Depends(get_db),
) -> GeneratedMessageResponse:
    try:
        return await run_in_threadpool(
            regenerate_message,
            db,
            message_id,
            request,
        )
    except MessageReviewServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@message_router.post("/{message_id}/send", response_model=SendApprovedMessageResponse)
async def send_generated_message(
    message_id: int,
    request: SendApprovedMessageRequest | None = None,
    db: Session = Depends(get_db),
) -> SendApprovedMessageResponse:
    try:
        return await run_in_threadpool(
            send_approved_message,
            db,
            message_id,
            request,
        )
    except MessageSendServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


router.include_router(store_router)
router.include_router(message_router)
