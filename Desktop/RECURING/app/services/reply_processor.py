from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import AppSettings, load_settings
from app.models import BuyerMemory, Customer, CustomerReply
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


def extract_style_signals(reply_text: str, settings: AppSettings) -> dict:
    try:
        prompt = STYLE_EXTRACTION_PROMPT.format(reply_text=reply_text[:500])
        raw = call_groq(settings=settings, prompt=prompt)
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
            cleaned = cleaned.rsplit("```", 1)[0]
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error(f"Style extraction JSON parse failed: {e}")
        return {}
    except Exception as e:
        logger.error(f"Style extraction failed: {e}", exc_info=True)
        return {}


def apply_signals_to_memory(memory: BuyerMemory, signals: dict) -> bool:
    changed = False

    if signals.get("vibe_label"):
        existing_tags = set(
            t.strip() for t in (memory.style_tags or "").split(",") if t.strip()
        )
        vibe = signals["vibe_label"].strip()
        if vibe and vibe not in existing_tags:
            existing_tags.add(vibe)
            memory.style_tags = ", ".join(sorted(existing_tags))
            changed = True

    if signals.get("style_tags"):
        existing_tags = set(
            t.strip() for t in (memory.style_tags or "").split(",") if t.strip()
        )
        new_tags = [t.strip() for t in signals["style_tags"].split(",") if t.strip()]
        for tag in new_tags:
            if tag and tag not in existing_tags:
                existing_tags.add(tag)
                changed = True
        if changed:
            memory.style_tags = ", ".join(sorted(existing_tags))

    if signals.get("favorite_color"):
        existing_colors = set(
            c.strip() for c in (memory.favorite_colors or "").split(",") if c.strip()
        )
        color = signals["favorite_color"].strip()
        if color and color not in existing_colors:
            existing_colors.add(color)
            memory.favorite_colors = ", ".join(sorted(existing_colors))
            changed = True

    if signals.get("wardrobe_gap"):
        existing = memory.interest_summary or ""
        gap = signals["wardrobe_gap"]
        if gap and gap not in existing:
            memory.interest_summary = (existing + f" Wants: {gap}.").strip()
            changed = True

    if signals.get("occasion_friction"):
        existing = memory.interest_summary or ""
        friction = signals["occasion_friction"]
        if friction and friction not in existing:
            memory.interest_summary = (
                existing + f" Struggles with: {friction}."
            ).strip()
            changed = True

    if signals.get("general_preference"):
        existing = memory.memory_summary or ""
        pref = signals["general_preference"]
        if pref and pref not in existing:
            memory.memory_summary = (existing + f" {pref}.").strip()
            changed = True

    return changed


def build_acknowledgment_reply(
    reply_text: str,
    signals: dict,
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
        return f"got it {first_name} — noted for next time."


def process_customer_reply(
    db: Session,
    customer_email: str,
    reply_text: str,
    subject: str | None = None,
    message_id: str | None = None,
    *,
    settings: AppSettings | None = None,
    send_acknowledgment: bool = True,
) -> dict:
    settings = settings or load_settings()

    customer = db.scalar(select(Customer).where(Customer.email == customer_email))
    if not customer:
        logger.warning(f"No customer found for email {customer_email}")
        return {"status": "skipped", "reason": "customer_not_found"}

    signals = extract_style_signals(reply_text, settings)
    logger.info(f"Extracted signals for {customer_email}: {signals}")

    memory = db.scalar(
        select(BuyerMemory).where(
            BuyerMemory.store_id == customer.store_id,
            BuyerMemory.customer_id == customer.id,
        )
    )

    if memory:
        changed = apply_signals_to_memory(memory, signals)
        if changed:
            memory.updated_at = datetime.now(timezone.utc)
            db.commit()

    customer_reply = CustomerReply(
        store_id=customer.store_id,
        customer_id=customer.id,
        inbound_text=reply_text,
        extracted_preferences_json=signals,
    )
    db.add(customer_reply)
    db.commit()
    db.refresh(customer_reply)

    ack_message = None
    if send_acknowledgment:
        customer_name = (
            f"{customer.first_name or ''} {customer.last_name or ''}".strip()
        )
        ack_message = build_acknowledgment_reply(
            reply_text, signals, customer_name, settings
        )
        customer_reply.response_text = ack_message
        db.commit()

        try:
            send_gmail_message(
                recipient_email=customer_email,
                subject="Re: your style",
                body_text=ack_message,
                settings=settings,
            )
        except Exception as e:
            logger.error(f"Failed to send acknowledgment: {e}", exc_info=True)

    return {
        "status": "processed",
        "customer_id": customer.id,
        "signals": signals,
        "acknowledgment": ack_message,
    }
