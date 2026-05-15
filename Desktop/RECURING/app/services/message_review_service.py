from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import AppSettings, load_settings
from app.models import Customer, GeneratedMessage, Store
from app.schemas import (
    GeneratedMessageResponse,
    GenerateMessagesRequest,
    RegenerateMessageRequest,
)
from app.services.message_engine import (
    ELIGIBLE_INTENTS,
    MessageEngineError,
    build_customer_message,
    build_prompt,
    call_groq,
    display_name,
    load_customers_by_id,
)
from app.services.recommendation_engine import (
    RecommendationEngineError,
    get_recommendations_for_customers,
)


DRAFT_STATUS = "draft"
APPROVED_STATUS = "approved"
REJECTED_STATUS = "rejected"
SENT_STATUS = "sent"
ACTIVE_DUPLICATE_STATUSES = {DRAFT_STATUS, APPROVED_STATUS}


class MessageReviewServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def generate_drafts_for_store(
    db: Session,
    store_id: int,
    request: GenerateMessagesRequest | None = None,
    *,
    settings: AppSettings | None = None,
) -> list[GeneratedMessageResponse]:
    request = request or GenerateMessagesRequest()
    settings = settings or load_settings()
    ensure_store_exists(db, store_id)

    try:
        recommendation_rows = get_recommendations_for_customers(
            db,
            store_id,
            customer_limit=5000,
            product_limit=1,
        )
    except RecommendationEngineError as exc:
        raise MessageReviewServiceError(str(exc), status_code=404) from exc

    candidates = [
        row
        for row in recommendation_rows
        if row.intent in ELIGIBLE_INTENTS and row.recommendations
    ]
    customer_ids = [row.customer_id for row in candidates]
    customers = load_customers_by_id(db, customer_ids)
    active_keys = load_active_message_keys(db, store_id, customer_ids)

    created: list[GeneratedMessage] = []
    try:
        for row in candidates:
            if len(created) >= request.limit:
                break

            product = row.recommendations[0]
            duplicate_key = (row.customer_id, product.product_id)
            if duplicate_key in active_keys:
                continue

            customer = customers.get(row.customer_id)
            if not customer:
                continue

            generated = build_customer_message(
                settings=settings,
                customer=customer,
                recommendation_row=row,
                product=product,
            )
            draft = GeneratedMessage(
                store_id=store_id,
                customer_id=row.customer_id,
                product_id=product.product_id,
                channel=request.channel,
                intent=row.intent,
                score=row.score,
                product_title=product.title,
                recommendation_reason=product.reason,
                message=generated.message,
                status=DRAFT_STATUS,
                provider="groq",
                model_name=settings.groq_model,
            )
            db.add(draft)
            db.flush()
            created.append(draft)
            active_keys.add(duplicate_key)

        db.commit()
    except MessageEngineError as exc:
        db.rollback()
        raise MessageReviewServiceError(str(exc), status_code=exc.status_code) from exc
    except Exception:
        db.rollback()
        raise

    return [to_response(draft, customers.get(draft.customer_id)) for draft in created]


def list_drafts(db: Session, store_id: int) -> list[GeneratedMessageResponse]:
    return list_messages_by_status(db, store_id, DRAFT_STATUS)


def list_approved_messages(db: Session, store_id: int) -> list[GeneratedMessageResponse]:
    return list_messages_by_status(db, store_id, APPROVED_STATUS)


def approve_message(db: Session, message_id: int) -> GeneratedMessageResponse:
    message = get_message_or_404(db, message_id)
    if message.status == SENT_STATUS:
        raise MessageReviewServiceError("Sent messages cannot be approved again.")

    now = utc_now()
    message.status = APPROVED_STATUS
    message.approved_at = now
    message.rejected_at = None
    message.updated_at = now
    db.commit()
    db.refresh(message)
    return to_response(message, db.get(Customer, message.customer_id))


def reject_message(db: Session, message_id: int) -> GeneratedMessageResponse:
    message = get_message_or_404(db, message_id)
    if message.status == SENT_STATUS:
        raise MessageReviewServiceError("Sent messages cannot be rejected.")

    now = utc_now()
    message.status = REJECTED_STATUS
    message.rejected_at = now
    message.approved_at = None
    message.updated_at = now
    db.commit()
    db.refresh(message)
    return to_response(message, db.get(Customer, message.customer_id))


def regenerate_message(
    db: Session,
    message_id: int,
    request: RegenerateMessageRequest | None = None,
    *,
    settings: AppSettings | None = None,
) -> GeneratedMessageResponse:
    request = request or RegenerateMessageRequest()
    settings = settings or load_settings()
    message = get_message_or_404(db, message_id)
    if message.status == SENT_STATUS:
        raise MessageReviewServiceError("Sent messages cannot be regenerated.")

    customer = db.get(Customer, message.customer_id)
    if not customer:
        raise MessageReviewServiceError(
            f"Customer {message.customer_id} was not found.",
            status_code=404,
        )
    if not message.product_title or not message.recommendation_reason:
        raise MessageReviewServiceError(
            "Message cannot be regenerated because product context is missing."
        )

    prompt = build_prompt(
        name=display_name(customer),
        intent=message.intent,
        product_title=message.product_title,
        reason=message.recommendation_reason,
    )

    try:
        new_text = call_groq(settings=settings, prompt=prompt)
    except MessageEngineError as exc:
        raise MessageReviewServiceError(str(exc), status_code=exc.status_code) from exc

    now = utc_now()
    message.message = new_text
    message.status = DRAFT_STATUS
    message.channel = request.channel or message.channel
    message.model_name = settings.groq_model
    message.approved_at = None
    message.rejected_at = None
    message.updated_at = now
    db.commit()
    db.refresh(message)
    return to_response(message, customer)


def list_messages_by_status(
    db: Session,
    store_id: int,
    status: str,
) -> list[GeneratedMessageResponse]:
    ensure_store_exists(db, store_id)
    messages = db.scalars(
        select(GeneratedMessage)
        .where(
            GeneratedMessage.store_id == store_id,
            GeneratedMessage.status == status,
        )
        .order_by(GeneratedMessage.created_at.desc(), GeneratedMessage.id.desc())
    ).all()
    customers = load_customers_by_id(db, [message.customer_id for message in messages])
    return [to_response(message, customers.get(message.customer_id)) for message in messages]


def ensure_store_exists(db: Session, store_id: int) -> Store:
    store = db.get(Store, store_id)
    if not store:
        raise MessageReviewServiceError(f"Store {store_id} was not found.", status_code=404)
    return store


def get_message_or_404(db: Session, message_id: int) -> GeneratedMessage:
    message = db.get(GeneratedMessage, message_id)
    if not message:
        raise MessageReviewServiceError(
            f"Generated message {message_id} was not found.",
            status_code=404,
        )
    return message


def load_active_message_keys(
    db: Session,
    store_id: int,
    customer_ids: list[int],
) -> set[tuple[int, int | None]]:
    if not customer_ids:
        return set()

    rows = db.execute(
        select(GeneratedMessage.customer_id, GeneratedMessage.product_id).where(
            GeneratedMessage.store_id == store_id,
            GeneratedMessage.customer_id.in_(customer_ids),
            GeneratedMessage.status.in_(ACTIVE_DUPLICATE_STATUSES),
        )
    ).all()
    return {(int(customer_id), product_id) for customer_id, product_id in rows}


def to_response(
    message: GeneratedMessage,
    customer: Customer | None,
) -> GeneratedMessageResponse:
    return GeneratedMessageResponse(
        id=message.id,
        store_id=message.store_id,
        customer_id=message.customer_id,
        customer_name=display_name(customer) if customer else f"Customer {message.customer_id}",
        product_id=message.product_id,
        channel=message.channel,
        intent=message.intent,
        score=message.score,
        product_title=message.product_title,
        recommendation_reason=message.recommendation_reason,
        message=message.message,
        status=message.status,
        provider=message.provider,
        model_name=message.model_name,
        created_at=message.created_at,
        updated_at=message.updated_at,
        approved_at=message.approved_at,
        rejected_at=message.rejected_at,
    )
