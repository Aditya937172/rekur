from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Customer,
    CustomerProfile,
    CustomerReply,
    EmailEngagement,
    RetentionSendLog,
    ReturnRefund,
    Store,
)
from app.schemas import (
    CustomerReplyCreate,
    CustomerReplyResponse,
    EmailEngagementCreate,
    EmailEngagementResponse,
    ReturnRefundCreate,
    ReturnRefundResponse,
    SilentCustomerEngagementSeedRequest,
    SilentCustomerEngagementSeedResponse,
)
from app.services.buyer_memory_service import get_buyer_memory
from app.services.message_engine import MessageEngineError, call_groq
from app.core.config import AppSettings, load_settings
from app.services.send_policy_service import record_retention_send


class RetentionDataServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


VALID_EMAIL_EVENTS = {
    "sent",
    "open",
    "click",
    "bounce",
    "unsubscribe",
    "spam",
    "dropped",
    "deferred",
}


def record_email_engagement(
    db: Session,
    store_id: int,
    request: EmailEngagementCreate,
) -> EmailEngagementResponse:
    ensure_store(db, store_id)
    event_type = request.event_type.strip().lower()
    if event_type not in VALID_EMAIL_EVENTS:
        raise RetentionDataServiceError(
            f"Unsupported email event_type '{request.event_type}'.",
            status_code=400,
        )

    send_log = resolve_send_log(
        db,
        store_id=store_id,
        send_log_id=request.send_log_id,
        provider_message_id=request.provider_message_id,
    )
    customer_id = request.customer_id
    if send_log:
        customer_id = send_log.customer_id
    elif customer_id is not None:
        ensure_customer(db, store_id, customer_id)
    elif request.email:
        customer = db.scalar(
            select(Customer).where(
                Customer.store_id == store_id,
                Customer.email == request.email,
            )
        )
        if customer:
            customer_id = customer.id

    if customer_id is None:
        raise RetentionDataServiceError(
            "Email engagement could not be mapped. Pass provider_message_id, send_log_id, customer_id, or customer email.",
            status_code=404,
        )

    campaign_type = request.campaign_type or (
        send_log.campaign_type if send_log else None
    )
    row = EmailEngagement(
        store_id=store_id,
        customer_id=customer_id,
        send_log_id=send_log.id if send_log else request.send_log_id,
        provider_message_id=(
            request.provider_message_id
            or (send_log.provider_message_id if send_log else None)
        ),
        campaign_type=campaign_type,
        event_type=event_type,
        url=request.url,
        metadata_json={
            **request.metadata,
            "provider": request.provider,
            "email": request.email,
        },
        timestamp=request.timestamp or utc_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return email_engagement_response(row)


def seed_silent_customer_engagement(
    db: Session,
    store_id: int,
    request: SilentCustomerEngagementSeedRequest,
) -> SilentCustomerEngagementSeedResponse:
    ensure_store(db, store_id)
    customer = resolve_customer_for_test_seed(db, store_id, request)
    now = utc_now()
    if request.last_purchase_days_ago is not None:
        customer.last_order_date = now - timedelta(days=request.last_purchase_days_ago)

    sent_count = request.sent_count
    open_count = min(request.open_count, sent_count)
    click_count = min(request.click_count, sent_count)
    send_logs: list[RetentionSendLog] = []
    for index in range(sent_count):
        sent_at = now - timedelta(days=min(59, sent_count - index))
        send_log = record_retention_send(
            db,
            store_id=store_id,
            customer_id=customer.id,
            campaign_type=request.campaign_type,
            trigger_reason="silent_customer_test_seed",
            subject=f"Silent customer seed {index + 1}",
            provider="gmail",
            provider_message_id=(
                f"gmail-seed-{store_id}-{customer.id}-{int(now.timestamp())}-{index}"
            ),
            metadata={"source": "silent_customer_seed"},
            sent_at=sent_at,
        )
        send_logs.append(send_log)

    for send_log in send_logs[:open_count]:
        db.add(
            EmailEngagement(
                store_id=store_id,
                customer_id=customer.id,
                send_log_id=send_log.id,
                provider_message_id=send_log.provider_message_id,
                campaign_type=send_log.campaign_type,
                event_type="open",
                metadata_json={"provider": "gmail", "source": "silent_customer_seed"},
                timestamp=(send_log.sent_at or now) + timedelta(minutes=5),
            )
        )

    for send_log in send_logs[:click_count]:
        db.add(
            EmailEngagement(
                store_id=store_id,
                customer_id=customer.id,
                send_log_id=send_log.id,
                provider_message_id=send_log.provider_message_id,
                campaign_type=send_log.campaign_type,
                event_type="click",
                url="https://example.com/local-test-click",
                metadata_json={"provider": "gmail", "source": "silent_customer_seed"},
                timestamp=(send_log.sent_at or now) + timedelta(minutes=8),
            )
        )

    db.commit()
    from app.services.retention_campaign_service import detect_silent_customers

    detected = any(
        row.customer_id == customer.id
        for row in detect_silent_customers(db, store_id, limit=1000)
    )
    return SilentCustomerEngagementSeedResponse(
        customer_id=customer.id,
        sent_created=sent_count,
        opens_created=open_count,
        clicks_created=click_count,
        detected_as_silent=detected,
    )


def record_return_refund(
    db: Session,
    store_id: int,
    request: ReturnRefundCreate,
) -> ReturnRefundResponse:
    ensure_store(db, store_id)
    if request.customer_id is not None:
        ensure_customer(db, store_id, request.customer_id)
    row = ReturnRefund(
        store_id=store_id,
        customer_id=request.customer_id,
        order_id=request.order_id,
        shopify_refund_id=request.shopify_refund_id,
        status=request.status,
        amount=request.amount,
        reason=request.reason,
        metadata_json=request.metadata,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return ReturnRefundResponse(
        id=row.id,
        store_id=row.store_id,
        customer_id=row.customer_id,
        order_id=row.order_id,
        status=row.status,
        amount=row.amount,
        created_at=row.created_at,
    )


def handle_customer_reply(
    db: Session,
    store_id: int,
    request: CustomerReplyCreate,
    *,
    settings: AppSettings | None = None,
) -> CustomerReplyResponse:
    settings = settings or load_settings()
    customer = ensure_customer(db, store_id, request.customer_id)
    profile = get_or_create_profile(db, store_id, customer.id)
    memory = get_buyer_memory(db, store_id, customer.id)
    extracted = extract_preferences(request.inbound_text)
    merge_preferences(profile, extracted)
    response_text = generate_reply_text(
        customer_name=display_name(customer),
        profile_summary=memory.memory_summary or "",
        inbound_text=request.inbound_text,
        settings=settings,
    )
    profile.conversation_history_json = list(profile.conversation_history_json or []) + [
        {"role": "customer", "content": request.inbound_text, "at": utc_now().isoformat()},
        {"role": "stylist", "content": response_text, "at": utc_now().isoformat()},
    ]
    profile.last_reply_at = utc_now()
    profile.updated_at = utc_now()

    row = CustomerReply(
        store_id=store_id,
        customer_id=customer.id,
        send_log_id=request.send_log_id,
        inbound_text=request.inbound_text,
        extracted_preferences_json=extracted,
        response_text=response_text,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return CustomerReplyResponse(
        id=row.id,
        customer_id=row.customer_id,
        extracted_preferences=row.extracted_preferences_json or {},
        response_text=row.response_text or "",
        created_at=row.created_at,
    )


def get_or_create_profile(db: Session, store_id: int, customer_id: int) -> CustomerProfile:
    profile = (
        db.query(CustomerProfile)
        .filter(
            CustomerProfile.store_id == store_id,
            CustomerProfile.customer_id == customer_id,
        )
        .first()
    )
    if profile:
        return profile
    profile = CustomerProfile(store_id=store_id, customer_id=customer_id)
    db.add(profile)
    db.flush()
    return profile


def extract_preferences(text: str) -> dict[str, Any]:
    lowered = text.lower()
    colors = [
        color
        for color in [
            "black",
            "white",
            "navy",
            "blue",
            "pink",
            "pastel",
            "olive",
            "green",
            "maroon",
            "cream",
            "beige",
            "yellow",
            "purple",
        ]
        if color in lowered
    ]
    dislikes = any(word in lowered for word in ["don't like", "dont like", "not into", "hate"])
    styles = [
        style
        for style in [
            "oversized",
            "streetwear",
            "formal",
            "minimal",
            "ethnic",
            "premium",
            "casual",
            "dressy",
        ]
        if style in lowered
    ]
    return {
        "mentioned_colors": colors,
        "mentioned_styles": styles,
        "negative_preference": dislikes,
        "raw_signal": text[:1000],
    }


def merge_preferences(profile: CustomerProfile, extracted: dict[str, Any]) -> None:
    dimensions = dict(profile.preference_dimensions_json or {})
    for key in ["mentioned_colors", "mentioned_styles"]:
        existing = set(dimensions.get(key) or [])
        existing.update(extracted.get(key) or [])
        dimensions[key] = sorted(existing)
    if extracted.get("negative_preference"):
        dimensions.setdefault("negative_signals", []).append(extracted.get("raw_signal"))
    profile.preference_dimensions_json = dimensions
    if dimensions.get("mentioned_colors"):
        profile.color_palette = ", ".join(dimensions["mentioned_colors"][:8])
    if dimensions.get("mentioned_styles"):
        profile.dominant_aesthetic = dimensions["mentioned_styles"][0]


def generate_reply_text(
    *,
    customer_name: str,
    profile_summary: str,
    inbound_text: str,
    settings: AppSettings,
) -> str:
    prompt = (
        "You are a friendly brand stylist replying to a customer message.\n"
        f"Customer: {customer_name}\n"
        f"Style profile: {profile_summary}\n"
        f"Customer reply: {inbound_text}\n\n"
        "Acknowledge what they said specifically.\n"
        "Update your understanding of their preferences.\n"
        "Reply in 2 to 3 sentences maximum.\n"
        "GenZ casual tone. Sound like a real person not a bot.\n"
        "If they asked a style question answer it using their wardrobe memory.\n"
        "End naturally. Do not ask more than one follow up question."
    )
    try:
        return call_groq(settings=settings, prompt=prompt)
    except MessageEngineError:
        return "got you, that helps a lot. i’ll keep that style note in mind for what i show you next."


def ensure_store(db: Session, store_id: int) -> Store:
    store = db.get(Store, store_id)
    if not store:
        raise RetentionDataServiceError(f"Store {store_id} was not found.", status_code=404)
    return store


def ensure_customer(db: Session, store_id: int, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if not customer or customer.store_id != store_id:
        raise RetentionDataServiceError(
            f"Customer {customer_id} was not found.",
            status_code=404,
        )
    return customer


def resolve_send_log(
    db: Session,
    *,
    store_id: int,
    send_log_id: int | None,
    provider_message_id: str | None,
) -> RetentionSendLog | None:
    send_log = None
    if send_log_id is not None:
        send_log = db.get(RetentionSendLog, send_log_id)
    elif provider_message_id:
        send_log = db.scalar(
            select(RetentionSendLog).where(
                RetentionSendLog.store_id == store_id,
                RetentionSendLog.provider_message_id == provider_message_id,
            )
        )

    if send_log and send_log.store_id != store_id:
        raise RetentionDataServiceError(
            "Email engagement send_log belongs to a different store.",
            status_code=409,
        )
    return send_log


def resolve_customer_for_test_seed(
    db: Session,
    store_id: int,
    request: SilentCustomerEngagementSeedRequest,
) -> Customer:
    if request.customer_id is not None:
        return ensure_customer(db, store_id, request.customer_id)
    if request.email:
        customer = db.scalar(
            select(Customer).where(
                Customer.store_id == store_id,
                Customer.email == request.email,
            )
        )
        if customer:
            return customer
    raise RetentionDataServiceError(
        "Pass customer_id or an email that belongs to a customer in this store.",
        status_code=404,
    )


def email_engagement_response(row: EmailEngagement) -> EmailEngagementResponse:
    return EmailEngagementResponse(
        id=row.id,
        store_id=row.store_id,
        customer_id=row.customer_id,
        send_log_id=row.send_log_id,
        provider_message_id=row.provider_message_id,
        event_type=row.event_type,
        campaign_type=row.campaign_type,
        url=row.url,
        timestamp=row.timestamp,
    )


def display_name(customer: Customer) -> str:
    name = " ".join(
        part for part in [customer.first_name, customer.last_name] if part
    ).strip()
    return name or customer.email or f"Customer {customer.id}"
