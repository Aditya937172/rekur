from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import AppSettings, load_settings
from app.models import BuyerMemory, GeneratedOutfitImage, Order, Product, Store
from app.schemas import (
    AnniversarySkippedCustomer,
    FirstOrderAnniversaryCampaignRequest,
    FirstOrderAnniversaryCampaignResponse,
)
from app.services.buyer_memory_service import (
    rebuild_buyer_memory_for_store,
    update_buyer_memory_for_customer,
)
from app.services.message_engine import MessageEngineError, call_groq
from app.services.outfit_service import (
    OutfitServiceError,
    generate_custom_outfit_for_customer,
    product_to_context,
)
from app.services.send_policy_service import (
    SendPolicyError,
    already_sent_anniversary_year,
    enforce_send_policy,
)
from app.models import Customer


FIRST_ORDER_ANNIVERSARY_TRIGGER = "first_order_anniversary"


class AnniversaryServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class AnniversaryCandidate:
    memory: BuyerMemory
    first_order: Order


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_first_order_anniversary_campaign(
    db: Session,
    store_id: int,
    request: FirstOrderAnniversaryCampaignRequest,
) -> FirstOrderAnniversaryCampaignResponse:
    settings = load_settings()
    store = db.get(Store, store_id)
    if not store:
        raise AnniversaryServiceError(
            f"Store {store_id} was not found.", status_code=404
        )

    today = utc_now().date()
    skipped: list[AnniversarySkippedCustomer] = []
    outfits = []
    generated = 0
    sent = 0
    processed = 0

    for candidate in load_candidates(db, store_id, request):
        if processed >= request.limit:
            break
        memory = candidate.memory
        processed += 1

        if not request.force and not is_anniversary_due(
            memory.first_order_at,
            today,
            days_window=request.days_window,
        ):
            skipped.append(
                AnniversarySkippedCustomer(
                    customer_id=memory.customer_id,
                    reason="first purchase anniversary is not due",
                )
            )
            continue

        if not request.force and already_sent_anniversary_year(
            db,
            store_id=store_id,
            customer_id=memory.customer_id,
            year=today.year,
        ):
            skipped.append(
                AnniversarySkippedCustomer(
                    customer_id=memory.customer_id,
                    reason=f"anniversary already sent in {today.year}",
                )
            )
            continue

        try:
            product_context = anniversary_product_context(
                db,
                store,
                memory,
                candidate.first_order,
            )
            if not product_context:
                skipped.append(
                    AnniversarySkippedCustomer(
                        customer_id=memory.customer_id,
                        reason="no first-order products found",
                    )
                )
                continue
            subject = anniversary_subject(product_context)
            body = anniversary_body(
                settings=settings,
                memory=memory,
                product_context=product_context,
            )
            if request.send_email:
                enforce_send_policy(
                    db,
                    store_id=store_id,
                    customer_id=memory.customer_id,
                    campaign_type="purchase_anniversary",
                    trigger_reason=FIRST_ORDER_ANNIVERSARY_TRIGGER,
                    force=request.force or bool(request.recipient_email),
                )
            outfit = generate_custom_outfit_for_customer(
                db,
                store_id=store_id,
                customer_id=memory.customer_id,
                order_id=candidate.first_order.id,
                product_context=product_context,
                trigger_reason=FIRST_ORDER_ANNIVERSARY_TRIGGER,
                prompt=anniversary_image_prompt(memory, product_context),
                email_subject=subject,
                email_body=body,
                send_email=request.send_email,
                recipient_email=request.recipient_email,
                settings=settings,
            )
        except (OutfitServiceError, SendPolicyError) as exc:
            skipped.append(
                AnniversarySkippedCustomer(
                    customer_id=memory.customer_id,
                    reason=str(exc),
                )
            )
            continue

        outfits.append(outfit)
        generated += 1
        if outfit.status == "sent":
            sent += 1

    return FirstOrderAnniversaryCampaignResponse(
        store_id=store_id,
        trigger_reason=FIRST_ORDER_ANNIVERSARY_TRIGGER,
        processed=processed,
        generated=generated,
        sent=sent,
        skipped=skipped,
        outfits=outfits,
    )


def anniversary_product_context(
    db: Session,
    store: Store,
    memory: BuyerMemory,
    first_order: Order,
) -> list[dict[str, object]]:
    from app.services.gender_service import resolve_wearer_gender

    first_products = [
        item.product for item in first_order.items if item.product is not None
    ]

    customer = db.query(Customer).filter(Customer.id == memory.customer_id).first()
    wearer_gender = resolve_wearer_gender(customer, first_products)

    similar_products = find_similar_products_to_first_purchase(
        db, store, first_products, wearer_gender, limit=3
    )

    chosen: list[Product] = []
    for product in first_products:
        if product.id not in {item.id for item in chosen}:
            chosen.append(product)

    for product in similar_products:
        if product.id not in {item.id for item in chosen}:
            chosen.append(product)
            if len(chosen) >= 4:
                break

    return [
        product_to_context(
            store,
            product,
            "first_purchase" if product in first_products else "recommended_similar",
        )
        for product in chosen
    ]


def find_similar_products_to_first_purchase(
    db: Session,
    store: Store,
    first_products: list[Product],
    customer_gender: str | None,
    *,
    limit: int = 3,
) -> list[Product]:
    if not first_products:
        return []

    first_product = first_products[0]
    first_tags = set(
        tag.strip().lower()
        for tag in (first_product.tags or "").split(",")
        if tag.strip()
    )
    first_category = infer_category(first_product)

    all_products_query = select(Product).where(
        Product.store_id == store.id, Product.id != first_product.id
    )
    all_products = db.scalars(
        all_products_query.order_by(Product.updated_at.desc())
    ).all()

    if customer_gender and customer_gender not in {"unisex", "mixed"}:
        gender_tag = f"gender_{customer_gender}"
        gender_products = [
            p for p in all_products if p.tags and gender_tag in p.tags.lower()
        ]
        other_products = [p for p in all_products if p not in gender_products]
        all_products = gender_products + other_products

    scored: list[tuple[int, Product]] = []
    for product in all_products:
        if product.id in {p.id for p in first_products}:
            continue

        score = 0
        product_tags = set(
            tag.strip().lower()
            for tag in (product.tags or "").split(",")
            if tag.strip()
        )
        tag_overlap = len(first_tags & product_tags)
        score += tag_overlap * 5

        product_category = infer_category(product)
        if first_category and product_category == first_category:
            score += 15

        for tag in product_tags:
            if "bestseller" in tag:
                score += 3
            if "new" in tag or "trending" in tag:
                score += 2

        if score > 0:
            scored.append((score, product))

    scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    return [product for _, product in scored[:limit]]


def infer_category(product: Product) -> str | None:
    if not product.title:
        return None
    text = f"{product.title} {product.tags or ''}".lower()
    categories = [
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
        "shorts",
        "sweater",
        "blazer",
        "coat",
        "skirt",
        "top",
        "bottom",
    ]
    for category in categories:
        if category in text:
            return category
    return None


def anniversary_image_prompt(
    memory: BuyerMemory,
    product_context: list[dict[str, object]],
) -> str:
    first_items = [
        item for item in product_context if item.get("role") == "first_purchase"
    ]
    similar_items = [
        item for item in product_context if item.get("role") == "recommended_similar"
    ]

    first_titles = ", ".join(
        str(item.get("title")) for item in first_items if item.get("title")
    )
    similar_titles = ", ".join(
        str(item.get("title")) for item in similar_items if item.get("title")
    )

    return (
        "Use the attached product reference images as visual anchors. "
        "Match the exact colors, fabrics, and styles shown in those images. "
        "One single fashion lookbook image, exactly 3 styling options in one cohesive triptych. "
        "This is a one-year first purchase anniversary celebration. "
        f"Customer's original first purchase: {first_titles}. "
        f"Current similar products they might love: {similar_titles}. "
        "Create an inspiring lookbook showing the first piece styled three ways with the similar products. "
        "Connect their style journey from first purchase to now. "
        "Show: casual daytime, elevated evening, weekend adventure. "
        "Warm, personal, celebratory, premium D2C clothing campaign. "
        "No text, no logos, no labels, no watermarks. "
        f"Customer style context: {memory.memory_summary or 'building their style identity'}"
    )
    return (
        "One single fashion lookbook image, exactly 3 styling options in one cohesive triptych. "
        "This is a one-year first purchase anniversary relationship moment, not a sale. "
        f"Use the customer's owned purchase-history pieces: {titles}. "
        "Show their best wardrobe combination styled three ways: coffee daytime, dinner evening, weekend outing. "
        "Warm, personal, stylish, premium D2C clothing campaign. "
        "No text, no logos, no labels, no watermarks. "
        f"Style identity context: {memory.memory_summary or ''}"
    )


def anniversary_subject(product_context: list[dict[str, object]]) -> str:
    first_item = next(
        (
            str(item.get("title"))
            for item in product_context
            if item.get("role") == "first_purchase"
        ),
        "first pick",
    )
    return f"remember when you picked the {first_item}"


def anniversary_body(
    *,
    settings: AppSettings,
    memory: BuyerMemory,
    product_context: list[dict[str, object]],
) -> str:
    first_item = next(
        (
            str(item.get("title"))
            for item in product_context
            if item.get("role") == "first_purchase"
        ),
        "your first piece",
    )
    similar_items = [
        str(item.get("title"))
        for item in product_context
        if item.get("role") == "recommended_similar" and item.get("title")
    ]

    similar_text = (
        ", ".join(similar_items[:3]) if similar_items else "some fresh pieces"
    )

    prompt = (
        "Write a one year anniversary email for a clothing brand customer.\n"
        f"Purchase history summary: {memory.memory_summary}\n"
        f"Their first purchase: {first_item}\n"
        f"Similar products they might love now: {similar_text}\n"
        "Celebrate their style journey from that first piece to now.\n"
        "Reference their first purchase naturally - no generic 'one year ago' phrasing.\n"
        "Mention the similar products as options to evolve that original style.\n"
        "Sound genuinely warm and personal, not promotional.\n"
        "No discounts. No selling. Pure relationship moment.\n"
        "GenZ casual tone. Max 5 sentences.\n"
        "End with something that makes them want to reply."
    )
    try:
        return call_groq(settings=settings, prompt=prompt)
    except MessageEngineError:
        similar_products_text = ""
        if similar_items:
            similar_products_text = f" there are some new {similar_text} that remind me of that first pick - same vibe, newer cut."

        return (
            f"small throwback: your first pick from us was {first_item}. "
            f"feels like you've built a solid wardrobe since then - {memory.favorite_categories or 'your style'} thing going on, "
            f"{memory.favorite_colors or 'a consistent palette'}.{similar_products_text} "
            "i made one image with three ways to style that first piece today. which version feels most you?"
        )
    prompt = (
        "Write a one year anniversary email for a clothing brand customer.\n"
        f"Purchase history summary: {memory.memory_summary}\n"
        f"Pieces in the image: {titles}\n"
        "Reference their specific purchase history naturally.\n"
        "Mention what their purchases reveal about their style identity.\n"
        "Sound genuinely warm and personal not promotional.\n"
        "No discounts. No selling. Pure relationship moment.\n"
        "GenZ casual tone. Max 6 sentences.\n"
        "End with something that makes them want to reply."
    )
    try:
        return call_groq(settings=settings, prompt=prompt)
    except MessageEngineError:
        first_item = next(
            (
                str(item.get("title"))
                for item in product_context
                if item.get("role") == "first_purchase"
            ),
            "your first piece",
        )
        return (
            f"small throwback: your first pick from us was {first_item}. "
            f"your wardrobe since then has this {memory.favorite_categories or 'very personal'} thing going on, "
            f"with {memory.favorite_colors or 'a palette'} that feels pretty consistent. "
            "i put together one image with three ways that first piece still fits your style now. "
            "which version feels most like you today?"
        )


def load_candidates(
    db: Session,
    store_id: int,
    request: FirstOrderAnniversaryCampaignRequest,
) -> list[AnniversaryCandidate]:
    if request.customer_id is not None:
        memory = update_buyer_memory_for_customer(db, store_id, request.customer_id)
        db.commit()
        first_order = load_first_order(db, store_id, memory.customer_id)
        return (
            [AnniversaryCandidate(memory=memory, first_order=first_order)]
            if first_order
            else []
        )

    memories = db.scalars(
        select(BuyerMemory)
        .where(
            BuyerMemory.store_id == store_id,
            BuyerMemory.first_order_at.is_not(None),
        )
        .order_by(BuyerMemory.first_order_at.asc(), BuyerMemory.customer_id.asc())
    ).all()
    if not memories:
        rebuild_buyer_memory_for_store(db, store_id)
        db.commit()
        memories = db.scalars(
            select(BuyerMemory)
            .where(
                BuyerMemory.store_id == store_id,
                BuyerMemory.first_order_at.is_not(None),
            )
            .order_by(BuyerMemory.first_order_at.asc(), BuyerMemory.customer_id.asc())
        ).all()

    candidates: list[AnniversaryCandidate] = []
    for memory in memories:
        first_order = load_first_order(db, store_id, memory.customer_id)
        if first_order:
            candidates.append(
                AnniversaryCandidate(memory=memory, first_order=first_order)
            )
    return candidates


def load_first_order(db: Session, store_id: int, customer_id: int) -> Order | None:
    return db.scalar(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.store_id == store_id, Order.customer_id == customer_id)
        .order_by(Order.created_at.asc(), Order.id.asc())
    )


def is_anniversary_due(
    first_order_at: datetime | None,
    today: date,
    *,
    days_window: int,
) -> bool:
    if not first_order_at:
        return False
    first_date = first_order_at.date()
    days_since_first_order = (today - first_date).days
    return abs(days_since_first_order - 365) <= days_window


def already_created_this_anniversary_year(
    db: Session,
    store_id: int,
    customer_id: int,
    year: int,
) -> bool:
    year_start = datetime(year, 1, 1, tzinfo=timezone.utc)
    existing_id = db.scalar(
        select(GeneratedOutfitImage.id)
        .where(
            GeneratedOutfitImage.store_id == store_id,
            GeneratedOutfitImage.customer_id == customer_id,
            GeneratedOutfitImage.trigger_reason == FIRST_ORDER_ANNIVERSARY_TRIGGER,
            GeneratedOutfitImage.created_at >= year_start,
            GeneratedOutfitImage.status.in_(["generated", "sent"]),
        )
        .limit(1)
    )
    return existing_id is not None
