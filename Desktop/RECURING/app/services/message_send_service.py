from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import AppSettings, load_settings
from app.core.observability import log_pipeline_event
from app.models import Customer, GeneratedMessage
from app.schemas import SendApprovedMessageRequest, SendApprovedMessageResponse
from app.services.gmail_service import GmailServiceError, send_gmail_message
from app.services.message_review_service import APPROVED_STATUS, SENT_STATUS
from app.services.send_policy_service import (
    SendPolicyError,
    enforce_send_policy,
    record_retention_send,
)


class MessageSendServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def send_approved_message(
    db: Session,
    message_id: int,
    request: SendApprovedMessageRequest | None = None,
    *,
    settings: AppSettings | None = None,
) -> SendApprovedMessageResponse:
    request = request or SendApprovedMessageRequest()
    settings = settings or load_settings()
    message = db.get(GeneratedMessage, message_id)
    if not message:
        raise MessageSendServiceError(
            f"Generated message {message_id} was not found.",
            status_code=404,
        )
    if message.status != APPROVED_STATUS:
        raise MessageSendServiceError(
            "Only approved messages can be sent.",
            status_code=400,
        )

    customer = db.get(Customer, message.customer_id)
    if not customer:
        raise MessageSendServiceError(
            f"Customer {message.customer_id} was not found.",
            status_code=404,
        )

    recipient_email = request.recipient_email or customer.email
    if not recipient_email:
        raise MessageSendServiceError(
            "Recipient email is missing. Add customer email or pass recipient_email for a test send.",
            status_code=400,
        )

    subject = request.subject or default_subject(message)
    try:
        enforce_send_policy(
            db,
            store_id=message.store_id,
            customer_id=message.customer_id,
            campaign_type="approved_message",
            trigger_reason="message_review",
            force=bool(request.recipient_email),
        )
    except SendPolicyError as exc:
        raise MessageSendServiceError(str(exc), status_code=exc.status_code) from exc

    try:
        gmail_response = send_gmail_message(
            recipient_email=recipient_email,
            subject=subject,
            body_text=message.message,
            settings=settings,
        )
    except GmailServiceError as exc:
        raise MessageSendServiceError(str(exc), status_code=exc.status_code) from exc

    message.status = SENT_STATUS
    message.updated_at = utc_now()
    record_retention_send(
        db,
        store_id=message.store_id,
        customer_id=message.customer_id,
        campaign_type="approved_message",
        trigger_reason="message_review",
        subject=subject,
        provider="gmail",
        provider_message_id=gmail_response.get("id"),
        metadata={
            "generated_message_id": message.id,
            "recipient_email": recipient_email,
        },
    )
    log_pipeline_event(
        "email_sent",
        pipeline="approved_message_send",
        provider="gmail",
        provider_message_id=gmail_response.get("id"),
        store_id=message.store_id,
        customer_id=message.customer_id,
        generated_message_id=message.id,
    )
    db.commit()

    return SendApprovedMessageResponse(
        message_id=message.id,
        status=message.status,
        provider="gmail",
        provider_message_id=gmail_response.get("id"),
        sender_email=settings.gmail_sender_email or "",
        recipient_email=recipient_email,
        customer_id=message.customer_id,
        subject=subject,
    )


def default_subject(message: GeneratedMessage) -> str:
    if message.product_title:
        return f"A quick note about {message.product_title}"
    return "A quick note from our store"
