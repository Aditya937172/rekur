from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    Customer,
    CustomerProfile,
    CustomerReply,
    EmailEngagement,
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
)
from app.services.buyer_memory_service import get_buyer_memory
from app.services.message_engine import MessageEngineError, call_groq
from app.core.config import AppSettings, load_settings


class RetentionDataServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def record_email_engagement(
    db: Session,
    store_id: int,
    request: EmailEngagementCreate,
) -> EmailEngagementResponse:
    ensure_store(db, store_id)
    if request.customer_id is not None:
        ensure_customer(db, store_id, request.customer_id)
    row = EmailEngagement(
        store_id=store_id,
        customer_id=request.customer_id,
        send_log_id=request.send_log_id,
        provider_message_id=request.provider_message_id,
        campaign_type=request.campaign_type,
        event_type=request.event_type,
        url=request.url,
        metadata_json=request.metadata,
        timestamp=request.timestamp or utc_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return EmailEngagementResponse(
        id=row.id,
        store_id=row.store_id,
        customer_id=row.customer_id,
        event_type=row.event_type,
        campaign_type=row.campaign_type,
        timestamp=row.timestamp,
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


def display_name(customer: Customer) -> str:
    name = " ".join(
        part for part in [customer.first_name, customer.last_name] if part
    ).strip()
    return name or customer.email or f"Customer {customer.id}"
