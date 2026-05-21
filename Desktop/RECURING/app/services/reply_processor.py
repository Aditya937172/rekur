from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import AppSettings, load_settings
from app.models import (
    BuyerMemory,
    Customer,
    CustomerProfile,
    CustomerReply,
    RetentionSendLog,
)
from app.services.buyer_memory_service import get_buyer_memory
from app.services.gmail_service import send_gmail_message
from app.services.message_engine import call_groq

logger = logging.getLogger(__name__)

STYLE_EXTRACTION_PROMPT = """A clothing brand customer replied to a marketing email. Extract their style preferences.

Customer reply: {reply_text}

Return a JSON object with these fields (use null if not mentioned):
{{
  "style_orientation": "minimalist|maximalist|balanced|null",
  "lifestyle": "casual|formal|mixed|null",
  "consideration_depth": "planned|spontaneous|null",
  "vibe_label": "string describing their aesthetic or null",
  "wardrobe_gap": "specific item they said they need or null",
  "occasion_friction": "occasion they struggle to dress for or null",
  "favorite_color": "color they mentioned or null",
  "style_tags": "list of style keywords from their reply or null",
  "general_preference": "any other style signal as a short string or null"
}}
Return only the JSON, no explanation."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def extract_style_signals(reply_text: str, settings: AppSettings) -> dict[str, Any]:
    try:
        prompt = STYLE_EXTRACTION_PROMPT.format(reply_text=reply_text[:500])
        raw = call_groq(settings=settings, prompt=prompt)
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
            cleaned = cleaned.rsplit("```", 1)[0]
        parsed = json.loads(cleaned)
        return normalize_signals(parsed if isinstance(parsed, dict) else {})
    except json.JSONDecodeError as exc:
        logger.error("Style extraction JSON parse failed: %s", exc)
        return fallback_style_signals(reply_text)
    except Exception as exc:
        logger.error("Style extraction failed: %s", exc, exc_info=True)
        return fallback_style_signals(reply_text)


def normalize_signals(signals: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(signals)
    normalized["style_tags"] = listify_signal(normalized.get("style_tags"))
    for key in [
        "style_orientation",
        "lifestyle",
        "consideration_depth",
        "vibe_label",
        "wardrobe_gap",
        "occasion_friction",
        "favorite_color",
        "general_preference",
    ]:
        if normalized.get(key) in {"", "null", "None"}:
            normalized[key] = None
    return normalized


def fallback_style_signals(reply_text: str) -> dict[str, Any]:
    lowered = reply_text.lower()
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
            "clean",
            "neutral",
        ]
        if style in lowered
    ]
    return {
        "style_orientation": "minimalist" if "minimal" in styles else None,
        "lifestyle": "formal"
        if "formal" in styles
        else ("casual" if "casual" in styles else None),
        "consideration_depth": None,
        "vibe_label": ", ".join(styles[:3]) if styles else None,
        "wardrobe_gap": None,
        "occasion_friction": None,
        "favorite_color": colors[0] if colors else None,
        "style_tags": styles,
        "general_preference": reply_text[:240] if reply_text.strip() else None,
    }


def listify_signal(value: Any) -> list[str]:
    if value in (None, "", "null"):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    text = str(value).strip()
    return [text] if text else []


def apply_signals_to_memory(memory: BuyerMemory, signals: dict[str, Any]) -> bool:
    changed = False

    if signals.get("vibe_label"):
        existing_tags = split_csv(memory.style_tags)
        vibe = str(signals["vibe_label"]).strip()
        if vibe and vibe not in existing_tags:
            existing_tags.add(vibe)
            memory.style_tags = ", ".join(sorted(existing_tags))
            changed = True

    new_tags = listify_signal(signals.get("style_tags"))
    if new_tags:
        existing_tags = split_csv(memory.style_tags)
        before = len(existing_tags)
        existing_tags.update(new_tags)
        memory.style_tags = ", ".join(sorted(existing_tags))
        changed = changed or len(existing_tags) != before

    if signals.get("favorite_color"):
        existing_colors = split_csv(memory.favorite_colors)
        color = str(signals["favorite_color"]).strip()
        if color and color not in existing_colors:
            existing_colors.add(color)
            memory.favorite_colors = ", ".join(sorted(existing_colors))
            changed = True

    if signals.get("wardrobe_gap"):
        existing = memory.interest_summary or ""
        gap = str(signals["wardrobe_gap"]).strip()
        if gap and gap not in existing:
            memory.interest_summary = (existing + f" Wants: {gap}.").strip()
            changed = True

    if signals.get("occasion_friction"):
        existing = memory.interest_summary or ""
        friction = str(signals["occasion_friction"]).strip()
        if friction and friction not in existing:
            memory.interest_summary = (
                existing + f" Struggles with: {friction}."
            ).strip()
            changed = True

    if signals.get("general_preference"):
        existing = memory.memory_summary or ""
        pref = str(signals["general_preference"]).strip()
        if pref and pref not in existing:
            memory.memory_summary = (existing + f" {pref}.").strip()
            changed = True

    if changed:
        memory.updated_at = utc_now()
    return changed


def split_csv(value: str | None) -> set[str]:
    return {item.strip() for item in (value or "").split(",") if item.strip()}


def get_or_create_profile(db: Session, customer: Customer) -> CustomerProfile:
    profile = db.scalar(
        select(CustomerProfile).where(
            CustomerProfile.store_id == customer.store_id,
            CustomerProfile.customer_id == customer.id,
        )
    )
    if profile:
        return profile
    profile = CustomerProfile(store_id=customer.store_id, customer_id=customer.id)
    db.add(profile)
    db.flush()
    return profile


def apply_signals_to_profile(
    profile: CustomerProfile,
    signals: dict[str, Any],
    *,
    reply_text: str,
    response_text: str | None,
) -> None:
    dimensions = dict(profile.preference_dimensions_json or {})

    for key in ["style_orientation", "lifestyle", "consideration_depth", "vibe_label"]:
        value = signals.get(key)
        if value:
            dimensions[key] = value

    for key, signal_key in [
        ("mentioned_styles", "style_tags"),
        ("wardrobe_gaps", "wardrobe_gap"),
        ("occasion_friction", "occasion_friction"),
        ("general_preferences", "general_preference"),
    ]:
        values = listify_signal(signals.get(signal_key))
        if not values:
            continue
        existing = set(dimensions.get(key) or [])
        existing.update(values)
        dimensions[key] = sorted(existing)

    if signals.get("favorite_color"):
        existing_colors = set(dimensions.get("mentioned_colors") or [])
        existing_colors.add(str(signals["favorite_color"]).strip())
        dimensions["mentioned_colors"] = sorted(
            color for color in existing_colors if color
        )

    profile.preference_dimensions_json = dimensions
    if dimensions.get("mentioned_colors"):
        profile.color_palette = ", ".join(dimensions["mentioned_colors"][:8])
    style_values = dimensions.get("mentioned_styles") or []
    profile.dominant_aesthetic = (
        signals.get("vibe_label")
        or (style_values[0] if style_values else profile.dominant_aesthetic)
    )
    profile.conversation_history_json = list(profile.conversation_history_json or []) + [
        {"role": "customer", "content": reply_text, "at": utc_now().isoformat()},
        {
            "role": "stylist" if response_text else "system",
            "content": response_text or "acknowledgment not sent",
            "at": utc_now().isoformat(),
        },
    ]
    profile.last_reply_at = utc_now()
    profile.updated_at = utc_now()


def build_acknowledgment_reply(
    reply_text: str,
    signals: dict[str, Any],
    customer_name: str,
    settings: AppSettings,
) -> str:
    prompt = (
        f"A clothing brand customer {customer_name} just replied to an email.\n"
        f"Their reply: {reply_text[:300]}\n"
        f"Extracted style signals: {signals}\n\n"
        "Write a 2-3 sentence acknowledgment reply in GenZ casual tone.\n"
        "Confirm you understood their preference.\n"
        "End with one natural follow-up question about their style.\n"
        "Sound like a friend, not a brand. No emojis. No formal language."
    )
    try:
        return call_groq(settings=settings, prompt=prompt)
    except Exception:
        first_name = customer_name.split()[0] if customer_name else "there"
        return (
            f"got it {first_name}, noted for next time. "
            "what kind of fit are you leaning toward lately?"
        )


def resolve_customer_by_reply_email(db: Session, customer_email: str) -> Customer | None:
    normalized = customer_email.strip().lower()
    customers = db.scalars(
        select(Customer).where(func.lower(Customer.email) == normalized)
    ).all()
    if not customers:
        return None
    if len(customers) == 1:
        return customers[0]

    latest_send = db.scalar(
        select(RetentionSendLog)
        .where(RetentionSendLog.customer_id.in_([customer.id for customer in customers]))
        .order_by(RetentionSendLog.sent_at.desc(), RetentionSendLog.id.desc())
        .limit(1)
    )
    if latest_send:
        return next(
            (customer for customer in customers if customer.id == latest_send.customer_id),
            customers[0],
        )
    return customers[0]


def latest_send_log_for_customer(
    db: Session,
    customer: Customer,
) -> RetentionSendLog | None:
    return db.scalar(
        select(RetentionSendLog)
        .where(
            RetentionSendLog.store_id == customer.store_id,
            RetentionSendLog.customer_id == customer.id,
        )
        .order_by(RetentionSendLog.sent_at.desc(), RetentionSendLog.id.desc())
        .limit(1)
    )


def process_customer_reply(
    db: Session,
    customer_email: str,
    reply_text: str,
    subject: str | None = None,
    message_id: str | None = None,
    *,
    settings: AppSettings | None = None,
    send_acknowledgment: bool = True,
) -> dict[str, Any]:
    settings = settings or load_settings()

    customer = resolve_customer_by_reply_email(db, customer_email)
    if not customer:
        logger.warning("No customer found for email %s", customer_email)
        return {"status": "skipped", "reason": "customer_not_found"}

    signals = extract_style_signals(reply_text, settings)
    logger.info("Extracted signals for %s: %s", customer_email, signals)

    memory = get_buyer_memory(db, customer.store_id, customer.id)
    apply_signals_to_memory(memory, signals)

    send_log = latest_send_log_for_customer(db, customer)
    customer_reply = CustomerReply(
        store_id=customer.store_id,
        customer_id=customer.id,
        send_log_id=send_log.id if send_log else None,
        inbound_text=reply_text,
        extracted_preferences_json=signals,
    )
    db.add(customer_reply)

    ack_message = None
    acknowledgment_status = "not_sent"
    if send_acknowledgment:
        customer_name = (
            f"{customer.first_name or ''} {customer.last_name or ''}".strip()
        )
        ack_message = build_acknowledgment_reply(
            reply_text,
            signals,
            customer_name,
            settings,
        )
        customer_reply.response_text = ack_message

        try:
            send_gmail_message(
                recipient_email=customer_email,
                subject=f"Re: {subject}" if subject else "Re: your style",
                body_text=ack_message,
                settings=settings,
            )
            acknowledgment_status = "sent"
        except Exception as exc:
            logger.error("Failed to send acknowledgment: %s", exc, exc_info=True)
            acknowledgment_status = "failed"

    profile = get_or_create_profile(db, customer)
    apply_signals_to_profile(
        profile,
        signals,
        reply_text=reply_text,
        response_text=ack_message,
    )
    db.commit()
    db.refresh(customer_reply)

    return {
        "status": "processed",
        "customer_id": customer.id,
        "customer_reply_id": customer_reply.id,
        "signals": signals,
        "acknowledgment": ack_message,
        "acknowledgment_status": acknowledgment_status,
    }
