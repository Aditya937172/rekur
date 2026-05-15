from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import AppSettings, PROJECT_ROOT, load_settings
from app.models import (
    BuyerMemory,
    Customer,
    GeneratedOutfitImage,
    Order,
    Product,
    Store,
)
from app.schemas import (
    GenerateOutfitImageRequest,
    GeneratedOutfitImageResponse,
    OutfitEmailSendResponse,
    SendOutfitEmailRequest,
)
from app.services.buyer_memory_service import (
    BuyerMemoryServiceError,
    get_buyer_memory,
    update_buyer_memory_for_customer,
)
from app.services.email_delivery_service import EmailDeliveryError, send_retention_email
from app.services.fashion_clip_service import FashionClipService, ProductSignal
from app.services.image_generation_service import (
    ImageGenerationServiceError,
    generate_outfit_image,
)
from app.services.runpod_seedream_service import (
    RunPodSeedreamError,
    generate_seedream_image,
)
from app.services.send_policy_service import (
    SendPolicyError,
    enforce_send_policy,
    record_retention_send,
)
from app.services.vector_cache_service import (
    product_combination_cache_key,
    search_outfit_image_cache,
    store_outfit_image_cache,
)
from app.services.gender_service import get_customer_gender


class OutfitServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def generate_outfit_for_customer(
    db: Session,
    store_id: int,
    request: GenerateOutfitImageRequest,
    *,
    settings: AppSettings | None = None,
) -> GeneratedOutfitImageResponse:
    settings = settings or load_settings()
    store = ensure_store(db, store_id)
    customer = ensure_customer(db, store_id, request.customer_id)
    memory = update_buyer_memory_for_customer(db, store_id, customer.id)
    order = resolve_order(db, store_id, customer.id, request.order_id, memory)
    purchased_products = products_from_order(order)
    recommended_products = select_pairing_products(
        db,
        store,
        memory,
        purchased_products,
        customer=customer,
    )
    product_context = build_product_context(
        store,
        purchased_products,
        recommended_products,
    )
    reference_urls = image_references(product_context)
    embedding = product_context_embedding(product_context, settings)
    cache_lookup = search_outfit_image_cache(
        db,
        store_id=store_id,
        trigger_reason=request.trigger_reason,
        embedding=embedding,
        settings=settings,
    )
    prompt = build_outfit_prompt(
        customer,
        memory,
        product_context,
        trigger_reason=request.trigger_reason,
    )

    outfit = GeneratedOutfitImage(
        store_id=store_id,
        customer_id=customer.id,
        order_id=order.id if order else None,
        buyer_memory_id=memory.id,
        trigger_reason=request.trigger_reason,
        status="generating",
        provider="gpt-image",
        model_name=settings.image_model,
        prompt=prompt,
        recommended_products_json=product_context,
        reference_image_urls_json=reference_urls,
        email_subject=default_outfit_subject(
            purchased_products,
            trigger_reason=request.trigger_reason,
        ),
        email_body=default_outfit_email_body(
            customer,
            memory,
            product_context,
            trigger_reason=request.trigger_reason,
        ),
    )
    db.add(outfit)
    db.flush()

    try:
        if cache_lookup.cache:
            outfit.provider = f"{cache_lookup.cache.provider or 'image'}_cache"
            outfit.model_name = cache_lookup.cache.model_name
            outfit.task_status = "cache_hit"
            outfit.task_progress = 100
            outfit.image_base64 = cache_lookup.cache.image_base64
            outfit.image_url = cache_lookup.cache.image_url
            outfit.status = "generated"
        else:
            generate_single_outfit_image(
                outfit=outfit,
                prompt=prompt,
                reference_urls=reference_urls,
                settings=settings,
            )
            store_outfit_image_cache(
                db,
                store_id=store_id,
                trigger_reason=request.trigger_reason,
                cache_key=product_combination_cache_key(
                    [int(item["product_id"]) for item in product_context]
                ),
                product_ids=[int(item["product_id"]) for item in product_context],
                embedding=embedding,
                image_url=outfit.image_url,
                image_base64=outfit.image_base64,
                provider=outfit.provider,
                model_name=outfit.model_name,
                metadata={
                    "task_id": outfit.task_id,
                    "task_status": outfit.task_status,
                    "cache_similarity": cache_lookup.similarity,
                },
            )
        outfit.status = "generated"
        outfit.updated_at = utc_now()
        db.commit()
    except (ImageGenerationServiceError, RunPodSeedreamError) as exc:
        outfit.status = "failed"
        outfit.error_message = str(exc)
        outfit.updated_at = utc_now()
        db.commit()
        raise OutfitServiceError(str(exc), status_code=exc.status_code) from exc

    db.refresh(outfit)
    if request.send_email:
        send_outfit_email(
            db,
            outfit.id,
            SendOutfitEmailRequest(recipient_email=request.recipient_email),
            settings=settings,
        )
        db.refresh(outfit)
    return to_outfit_response(outfit)


def list_outfits(
    db: Session,
    store_id: int,
    *,
    status: str | None = None,
) -> list[GeneratedOutfitImageResponse]:
    ensure_store(db, store_id)
    query = select(GeneratedOutfitImage).where(
        GeneratedOutfitImage.store_id == store_id
    )
    if status:
        query = query.where(GeneratedOutfitImage.status == status)
    outfits = db.scalars(
        query.order_by(
            GeneratedOutfitImage.created_at.desc(),
            GeneratedOutfitImage.id.desc(),
        )
    ).all()
    return [to_outfit_response(outfit) for outfit in outfits]


def generate_custom_outfit_for_customer(
    db: Session,
    *,
    store_id: int,
    customer_id: int,
    order_id: int | None,
    product_context: list[dict[str, Any]],
    trigger_reason: str,
    prompt: str,
    email_subject: str,
    email_body: str,
    send_email: bool = False,
    recipient_email: str | None = None,
    settings: AppSettings | None = None,
) -> GeneratedOutfitImageResponse:
    settings = settings or load_settings()
    ensure_store(db, store_id)
    customer = ensure_customer(db, store_id, customer_id)
    memory = update_buyer_memory_for_customer(db, store_id, customer.id)
    reference_urls = image_references(product_context)
    embedding = product_context_embedding(product_context, settings)
    cache_lookup = search_outfit_image_cache(
        db,
        store_id=store_id,
        trigger_reason=trigger_reason,
        embedding=embedding,
        settings=settings,
    )
    outfit = GeneratedOutfitImage(
        store_id=store_id,
        customer_id=customer.id,
        order_id=order_id,
        buyer_memory_id=memory.id,
        trigger_reason=trigger_reason,
        status="generating",
        provider="gpt-image",
        model_name=settings.image_model,
        prompt=prompt,
        recommended_products_json=product_context,
        reference_image_urls_json=reference_urls,
        email_subject=email_subject,
        email_body=email_body,
    )
    db.add(outfit)
    db.flush()

    try:
        if cache_lookup.cache:
            outfit.provider = f"{cache_lookup.cache.provider or 'image'}_cache"
            outfit.model_name = cache_lookup.cache.model_name
            outfit.task_status = "cache_hit"
            outfit.task_progress = 100
            outfit.image_base64 = cache_lookup.cache.image_base64
            outfit.image_url = cache_lookup.cache.image_url
        else:
            generate_single_outfit_image(
                outfit=outfit,
                prompt=prompt,
                reference_urls=reference_urls,
                settings=settings,
            )
            product_ids = [
                int(item["product_id"])
                for item in product_context
                if item.get("product_id") is not None
            ]
            store_outfit_image_cache(
                db,
                store_id=store_id,
                trigger_reason=trigger_reason,
                cache_key=product_combination_cache_key(product_ids),
                product_ids=product_ids,
                embedding=embedding,
                image_url=outfit.image_url,
                image_base64=outfit.image_base64,
                provider=outfit.provider,
                model_name=outfit.model_name,
                metadata={
                    "task_id": outfit.task_id,
                    "task_status": outfit.task_status,
                    "cache_similarity": cache_lookup.similarity,
                },
            )
        outfit.status = "generated"
        outfit.updated_at = utc_now()
        db.commit()
    except (ImageGenerationServiceError, RunPodSeedreamError) as exc:
        outfit.status = "failed"
        outfit.error_message = str(exc)
        outfit.updated_at = utc_now()
        db.commit()
        raise OutfitServiceError(str(exc), status_code=exc.status_code) from exc

    db.refresh(outfit)
    if send_email:
        send_outfit_email(
            db,
            outfit.id,
            SendOutfitEmailRequest(recipient_email=recipient_email),
            settings=settings,
        )
        db.refresh(outfit)
    return to_outfit_response(outfit)


def send_outfit_email(
    db: Session,
    outfit_id: int,
    request: SendOutfitEmailRequest | None = None,
    *,
    settings: AppSettings | None = None,
) -> OutfitEmailSendResponse:
    request = request or SendOutfitEmailRequest()
    settings = settings or load_settings()
    outfit = db.get(GeneratedOutfitImage, outfit_id)
    if not outfit:
        raise OutfitServiceError(
            f"Outfit image {outfit_id} was not found.", status_code=404
        )
    if outfit.status not in {"generated", "email_failed"}:
        raise OutfitServiceError("Only generated outfit images can be emailed.")

    customer = db.get(Customer, outfit.customer_id)
    if not customer:
        raise OutfitServiceError(
            f"Customer {outfit.customer_id} was not found.",
            status_code=404,
        )
    recipient_email = request.recipient_email or customer.email
    if not recipient_email:
        raise OutfitServiceError(
            "Recipient email is missing. Pass recipient_email for a test send.",
            status_code=400,
        )
    campaign_type = campaign_type_for_trigger(outfit.trigger_reason)
    try:
        enforce_send_policy(
            db,
            store_id=outfit.store_id,
            customer_id=outfit.customer_id,
            campaign_type=campaign_type,
            force=bool(request.recipient_email),
        )
    except SendPolicyError as exc:
        raise OutfitServiceError(str(exc), status_code=exc.status_code) from exc

    subject = request.subject or outfit.email_subject or "Your outfit idea"
    body = outfit.email_body or "Here is a personalized outfit idea for you."
    try:
        inline_images = outfit_inline_images(outfit)
        email_response = send_retention_email(
            recipient_email=recipient_email,
            subject=subject,
            body_text=body,
            body_html=outfit_email_html(body, inline_images),
            attachments=[],
            inline_images=inline_images,
            settings=settings,
        )
    except EmailDeliveryError as exc:
        outfit.status = "email_failed"
        outfit.error_message = str(exc)
        outfit.updated_at = utc_now()
        db.commit()
        raise OutfitServiceError(str(exc), status_code=exc.status_code) from exc

    outfit.status = "sent"
    outfit.sent_at = utc_now()
    outfit.updated_at = utc_now()
    record_retention_send(
        db,
        store_id=outfit.store_id,
        customer_id=outfit.customer_id,
        campaign_type=campaign_type,
        trigger_reason=outfit.trigger_reason,
        subject=subject,
        provider=email_response.get("provider"),
        provider_message_id=email_response.get("id"),
        outfit_image_id=outfit.id,
        metadata={"recipient_email": recipient_email},
    )
    db.commit()
    return OutfitEmailSendResponse(
        outfit_id=outfit.id,
        status=outfit.status,
        provider_message_id=email_response.get("id"),
        recipient_email=recipient_email,
        subject=subject,
    )


def campaign_type_for_trigger(trigger_reason: str | None) -> str:
    if trigger_reason == "first_order_anniversary":
        return "purchase_anniversary"
    if trigger_reason == "seasonal_lookbook":
        return "seasonal_lookbook"
    if trigger_reason == "pre_churn_stage_1":
        return "pre_churn"
    if trigger_reason == "silent_customer":
        return "silent_customer"
    return "post_purchase_outfit"


def generate_single_outfit_image(
    *,
    outfit: GeneratedOutfitImage,
    prompt: str,
    reference_urls: list[str],
    settings: AppSettings,
) -> None:
    provider = settings.image_provider.strip().lower()
    if provider in {"runpod", "runpod_seedream", "seedream", "seedream_v4"}:
        result = generate_seedream_image(prompt=prompt, settings=settings)
        outfit.provider = "runpod_seedream"
        outfit.model_name = "seedream-v4"
        outfit.task_id = result.job_id
        outfit.task_status = result.status
        outfit.task_progress = 100
        outfit.image_base64 = result.image_base64
        outfit.image_url = result.image_url
    else:
        result = generate_outfit_image(
            prompt=prompt,
            image_urls=reference_urls,
            settings=settings,
        )
        outfit.provider = "gpt-image"
        outfit.model_name = settings.image_model
        outfit.task_id = result.task_id
        outfit.task_status = result.task_status
        outfit.task_progress = result.task_progress
        outfit.image_base64 = result.image_base64
        outfit.image_url = result.image_url
    if outfit.image_base64 and not outfit.image_url:
        outfit.image_url = save_base64_image(outfit.id, outfit.image_base64)


def ensure_store(db: Session, store_id: int) -> Store:
    store = db.get(Store, store_id)
    if not store:
        raise OutfitServiceError(f"Store {store_id} was not found.", status_code=404)
    return store


def ensure_customer(db: Session, store_id: int, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if not customer or customer.store_id != store_id:
        raise OutfitServiceError(
            f"Customer {customer_id} was not found.", status_code=404
        )
    return customer


def resolve_order(
    db: Session,
    store_id: int,
    customer_id: int,
    order_id: int | None,
    memory: BuyerMemory,
) -> Order | None:
    target_order_id = order_id or memory.last_order_id
    if not target_order_id:
        return None
    order = db.scalar(
        select(Order)
        .options(selectinload(Order.items))
        .where(
            Order.id == target_order_id,
            Order.store_id == store_id,
            Order.customer_id == customer_id,
        )
    )
    if not order:
        raise OutfitServiceError(
            f"Order {target_order_id} was not found.", status_code=404
        )
    return order


def products_from_order(order: Order | None) -> list[Product]:
    if not order:
        return []
    return [item.product for item in order.items if item.product is not None]


def select_pairing_products(
    db: Session,
    store: Store,
    memory: BuyerMemory,
    purchased_products: list[Product],
    *,
    customer: Customer | None = None,
    limit: int = 3,
) -> list[Product]:
    purchased_ids = {product.id for product in purchased_products}
    categories = {
        infer_category_from_text(f"{product.title} {product.tags or ''}")
        for product in purchased_products
    }
    wanted = pairing_categories(categories)

    customer_gender = None
    if customer:
        customer_gender = get_customer_gender(db, customer)

    all_products_query = select(Product).where(
        Product.store_id == store.id, Product.id.not_in(purchased_ids or {-1})
    )
    all_products = db.scalars(
        all_products_query.order_by(Product.updated_at.desc())
    ).all()

    if customer_gender and customer_gender != "unisex":
        gender_tag = f"gender_{customer_gender}"
        gender_products = [
            p for p in all_products if p.tags and gender_tag in p.tags.lower()
        ]
        other_products = [p for p in all_products if p not in gender_products]
        all_products = gender_products + other_products

    scored: list[tuple[int, Product]] = []
    clip = FashionClipService()
    anchor_signals = [
        ProductSignal(
            product_id=product.id,
            title=product.title,
            tags=product.tags,
            image_url=product.image_url,
        )
        for product in purchased_products
    ]
    interest_titles = {
        str(item.get("title", "")).lower()
        for item in (memory.recent_interests_json or [])
    }
    for product in all_products:
        text = f"{product.title} {product.tags or ''}".lower()
        score = 0
        if any(category and category in text for category in wanted):
            score += 10
        if product.title.lower() in interest_titles:
            score += 6
        for tag in split_words(memory.style_tags):
            if tag and tag in text:
                score += 2
        for color in split_words(memory.favorite_colors):
            if color and color in text:
                score += 1
        if anchor_signals:
            candidate_signal = ProductSignal(
                product_id=product.id,
                title=product.title,
                tags=product.tags,
                image_url=product.image_url,
            )
            semantic_score = max(
                clip.compatibility_score(anchor, candidate_signal)
                for anchor in anchor_signals
            )
            score += int(max(semantic_score, 0.0) * 20)
        if score > 0:
            scored.append((score, product))

    scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    chosen = [product for _, product in scored[:limit]]
    if len(chosen) >= limit:
        return chosen

    supplementary = select_supplementary_vibe_products(
        all_products=all_products,
        purchased_products=purchased_products,
        memory=memory,
        already_selected_ids={product.id for product in chosen},
        limit=limit - len(chosen),
    )
    chosen.extend(supplementary)

    if len(chosen) < limit:
        for product in all_products:
            if product.id in {item.id for item in chosen}:
                continue
            chosen.append(product)
            if len(chosen) >= limit:
                break
    return chosen


def select_supplementary_vibe_products(
    *,
    all_products: list[Product],
    purchased_products: list[Product],
    memory: BuyerMemory,
    already_selected_ids: set[int],
    limit: int,
) -> list[Product]:
    if limit <= 0:
        return []

    anchor_categories = {
        infer_category_from_text(f"{product.title} {product.tags or ''}")
        for product in purchased_products
    }
    anchor_words = set()
    for product in purchased_products:
        anchor_words.update(style_tokens(f"{product.title} {product.tags or ''}"))
    anchor_words.update(split_words(memory.style_tags))
    anchor_words.update(split_words(memory.favorite_colors))

    scored: list[tuple[int, Product]] = []
    for product in all_products:
        if product.id in already_selected_ids:
            continue
        text = f"{product.title} {product.tags or ''}"
        product_category = infer_category_from_text(text)
        tokens = set(style_tokens(text))
        score = 0
        if product_category and product_category in anchor_categories:
            score += 12
        score += len(tokens & anchor_words) * 3
        if same_vibe(text, memory):
            score += 6
        if score > 0:
            scored.append((score, product))

    scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    return [product for _, product in scored[:limit]]


def pairing_categories(categories: set[str | None]) -> set[str]:
    pairings = {
        "shirt": {"jeans", "trousers", "jacket", "accessories"},
        "tee": {"jeans", "cargos", "jacket", "accessories"},
        "t-shirt": {"jeans", "cargos", "jacket", "accessories"},
        "jeans": {"shirt", "tee", "t-shirt", "hoodie", "jacket"},
        "trousers": {"shirt", "jacket", "accessories"},
        "cargos": {"tee", "hoodie", "jacket"},
        "dress": {"jacket", "accessories"},
        "hoodie": {"cargos", "jeans", "accessories"},
        "jacket": {"tee", "shirt", "jeans", "dress"},
        "ethnic": {"accessories", "trousers"},
    }
    wanted: set[str] = set()
    for category in categories:
        if category:
            wanted.update(pairings.get(category, set()))
    return wanted or {"shirt", "jeans", "jacket", "accessories"}


def build_product_context(
    store: Store,
    purchased_products: list[Product],
    recommended_products: list[Product],
) -> list[dict[str, Any]]:
    context: list[dict[str, Any]] = []
    for product in purchased_products:
        context.append(product_to_context(store, product, "purchased"))
    for product in recommended_products:
        context.append(product_to_context(store, product, "recommended_pairing"))
    return context


def product_to_context(store: Store, product: Product, role: str) -> dict[str, Any]:
    return {
        "product_id": product.id,
        "shopify_product_id": product.shopify_product_id,
        "role": role,
        "title": product.title,
        "tags": [tag.strip() for tag in (product.tags or "").split(",") if tag.strip()],
        "image_url": product.image_url,
        "product_url": product_url(store, product),
    }


def product_url(store: Store, product: Product) -> str:
    domain = (
        store.shopify_store_domain.replace("https://", "")
        .replace("http://", "")
        .strip("/")
    )
    handle = product.handle or slugify(product.title)
    return f"https://{domain}/products/{handle}"


def image_references(product_context: list[dict[str, Any]]) -> list[str]:
    return []


def product_context_embedding(
    product_context: list[dict[str, Any]],
    settings: AppSettings,
) -> list[float]:
    service = FashionClipService(settings)
    signals = [
        ProductSignal(
            product_id=int(item["product_id"]),
            title=str(item.get("title") or ""),
            tags=", ".join(str(tag) for tag in item.get("tags") or []),
            image_url=item.get("image_url"),
        )
        for item in product_context
        if item.get("product_id") is not None
    ]
    return service.embed_product_combination(signals)


def build_outfit_prompt(
    customer: Customer,
    memory: BuyerMemory,
    product_context: list[dict[str, Any]],
    *,
    trigger_reason: str = "order_delivered_followup",
) -> str:
    customer_name = (
        " ".join(
            part for part in [customer.first_name, customer.last_name] if part
        ).strip()
        or "the customer"
    )
    purchased = [item for item in product_context if item["role"] == "purchased"]
    pairings = [
        item for item in product_context if item["role"] == "recommended_pairing"
    ]
    has_complementary = has_cross_category_pairings(purchased, pairings)
    purchased_title = product_titles(purchased)
    pairing_title = product_titles(pairings)
    strategy = (
        "complementary outfit pairings"
        if has_complementary
        else "same-vibe supplementary styling options from the brand"
    )
    if trigger_reason == "first_order_anniversary":
        return (
            "One fashion lookbook image, exactly 3 outfit ideas in a clean triptych. "
            f"First purchase anniversary anchor item: {purchased_title}. "
            f"Recommendation strategy: {strategy}. "
            f"Style that first-order item with these 3 current store products: {pairing_title}. "
            "Mood: warm throwback, modern refresh, casual friend recommendation. "
            "Vibes: coffee daytime, dinner evening, weekend outing. "
            "Realistic attractive models, premium D2C clothing campaign, natural poses. "
            "No text, no logos, no labels, no watermarks, no distorted hands/faces. "
            f"Customer style memory: {compact(memory.memory_summary, 700)}"
        )
    return (
        "One fashion lookbook image, exactly 3 outfit ideas in a clean triptych. "
        f"Anchor item just delivered: {purchased_title}. "
        f"Recommendation strategy: {strategy}. "
        f"Style it with these 3 store products: {pairing_title}. "
        "Vibes: city daytime, dinner evening, weekend travel. "
        "Realistic attractive models, premium D2C clothing campaign, natural poses. "
        "No text, no logos, no labels, no watermarks, no distorted hands/faces. "
        f"Customer style memory: {compact(memory.memory_summary, 700)}"
    )


def default_outfit_subject(
    purchased_products: list[Product],
    *,
    trigger_reason: str = "order_delivered_followup",
) -> str:
    if trigger_reason == "first_order_anniversary":
        if purchased_products:
            return f"A new way to wear your first {purchased_products[0].title}"
        return "A small throwback outfit idea"
    if purchased_products:
        return f"An outfit idea for your {purchased_products[0].title}"
    return "A personalized outfit idea for you"


def default_outfit_email_body(
    customer: Customer,
    memory: BuyerMemory,
    product_context: list[dict[str, Any]],
    *,
    trigger_reason: str = "order_delivered_followup",
) -> str:
    name = customer.first_name or "there"
    purchased = [item for item in product_context if item.get("role") == "purchased"]
    recommended = [
        item for item in product_context if item.get("role") == "recommended_pairing"
    ]
    if trigger_reason == "first_order_anniversary":
        first_item = product_titles(purchased) or "your first pick"
        lines = [
            f"Hey {name},",
            "",
            anniversary_memory_line(memory, first_item),
            "I made one fresh outfit idea around that first buy, with a few pieces from the store that still fit your vibe.",
        ]
        if recommended:
            lines.append("")
            lines.append("The 3 pieces I would pair with it now:")
            for item in recommended[:3]:
                lines.append(f"- {item['title']}: {item['product_url']}")
        return "\n".join(lines)

    lines = [
        f"Hey {name},",
        "",
        "your order should be with you now, so I put together one quick styling idea for it.",
        "I attached one image with three different ways you can wear it, using a few pieces that pair well with what you picked.",
    ]
    if recommended:
        lines.append("")
        lines.append("The 3 pieces I would pair with it:")
        for item in recommended[:3]:
            lines.append(f"- {item['title']}: {item['product_url']}")
    return "\n".join(lines)


def anniversary_memory_line(memory: BuyerMemory, first_item: str) -> str:
    if not memory.first_order_at:
        return f"small throwback: your first pick from us was {first_item}."
    today = utc_now().date()
    years = max(today.year - memory.first_order_at.date().year, 0)
    if years == 1:
        return f"last year around this time, your first pick from us was {first_item}."
    if years > 1:
        return f"{years} years ago around this time, your first pick from us was {first_item}."
    return f"small throwback: your first pick from us was {first_item}."


def outfit_image_attachments(outfit: GeneratedOutfitImage) -> list[dict[str, Any]]:
    if outfit.image_base64:
        return [
            {
                "filename": f"outfit-{outfit.id}.png",
                "mime_type": "image/png",
                "content": base64.b64decode(outfit.image_base64),
            }
        ]
    if not outfit.image_url:
        return []
    response = requests.get(outfit.image_url, timeout=30)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type") or "image/png"
    extension = "jpg" if "jpeg" in content_type else "png"
    return [
        {
            "filename": f"outfit-{outfit.id}.{extension}",
            "mime_type": content_type,
            "content": response.content,
        }
    ]


def outfit_inline_images(outfit: GeneratedOutfitImage) -> list[dict[str, Any]]:
    images = outfit_image_attachments(outfit)
    if not images:
        return []
    image = images[0]
    image["cid"] = "outfit-image"
    return [image]


def outfit_email_html(
    body_text: str, inline_images: list[dict[str, Any]]
) -> str | None:
    if not inline_images:
        return None
    escaped = (
        body_text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
    return (
        "<html><body>"
        f"<p>{escaped}</p>"
        '<p><img src="cid:outfit-image" alt="Personalized outfit idea" '
        'style="max-width:640px;width:100%;height:auto;border:0;display:block;"></p>'
        "</body></html>"
    )


def save_base64_image(outfit_id: int, image_base64: str) -> str:
    output_dir = PROJECT_ROOT / "public" / "generated" / "outfits"
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"outfit_{outfit_id}.png"
    file_path.write_bytes(base64.b64decode(image_base64))
    return f"/public/generated/outfits/outfit_{outfit_id}.png"


def to_outfit_response(outfit: GeneratedOutfitImage) -> GeneratedOutfitImageResponse:
    return GeneratedOutfitImageResponse(
        id=outfit.id,
        store_id=outfit.store_id,
        customer_id=outfit.customer_id,
        order_id=outfit.order_id,
        buyer_memory_id=outfit.buyer_memory_id,
        trigger_reason=outfit.trigger_reason,
        status=outfit.status,
        provider=outfit.provider,
        model_name=outfit.model_name,
        task_id=outfit.task_id,
        task_status=outfit.task_status,
        task_progress=outfit.task_progress,
        prompt=outfit.prompt,
        image_url=outfit.image_url,
        recommended_products_json=outfit.recommended_products_json or [],
        reference_image_urls_json=outfit.reference_image_urls_json or [],
        email_subject=outfit.email_subject,
        email_body=outfit.email_body,
        error_message=outfit.error_message,
        created_at=outfit.created_at,
        updated_at=outfit.updated_at,
        sent_at=outfit.sent_at,
    )


def product_titles(items: list[dict[str, Any]]) -> str:
    titles = [str(item.get("title")) for item in items if item.get("title")]
    return ", ".join(titles) if titles else "none"


def split_words(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip().lower() for part in value.split(",") if part.strip()]


def infer_category_from_text(value: str) -> str | None:
    text = value.lower()
    for category in [
        "shirt",
        "t-shirt",
        "tee",
        "jeans",
        "trousers",
        "cargos",
        "dress",
        "hoodie",
        "jacket",
        "ethnic",
        "accessories",
    ]:
        if category in text:
            return category
    return None


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "product"


def compact(value: str | None, limit: int) -> str:
    if not value:
        return ""
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def style_tokens(value: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", value.lower().replace("_", " "))
    stop = {
        "seeded",
        "retention",
        "app",
        "seed",
        "product",
        "gender",
        "price",
        "tier",
        "color",
        "occasion",
    }
    return [word for word in words if len(word) > 2 and word not in stop]


def same_vibe(product_text: str, memory: BuyerMemory) -> bool:
    text = product_text.lower()
    signals = " ".join(
        value or ""
        for value in [
            memory.style_tags,
            memory.favorite_colors,
            memory.favorite_categories,
        ]
    ).lower()
    return any(token in text for token in style_tokens(signals))


def has_cross_category_pairings(
    purchased: list[dict[str, Any]],
    pairings: list[dict[str, Any]],
) -> bool:
    purchased_categories = {
        infer_category_from_text(
            f"{item.get('title', '')} {' '.join(item.get('tags') or [])}"
        )
        for item in purchased
    }
    pairing_categories = {
        infer_category_from_text(
            f"{item.get('title', '')} {' '.join(item.get('tags') or [])}"
        )
        for item in pairings
    }
    purchased_categories.discard(None)
    pairing_categories.discard(None)
    return bool(purchased_categories and pairing_categories - purchased_categories)
