"""
Enhanced Seasonal Lookbook Service.
Generates seasonal outfit combinations from customer's wardrobe using FashionCLIP.
"""

from datetime import datetime, timezone
from typing import Any, Optional
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import AppSettings, load_settings
from app.models import (
    BuyerMemory,
    Customer,
    GeneratedOutfitImage,
    Product,
    Store,
)
from app.schemas.outfit import GeneratedOutfitImageResponse
from app.services.buyer_memory_service import get_buyer_memory
from app.services.fashion_clip_service import FashionClipService, ProductSignal
from app.services.gender_service import get_customer_gender
from app.services.message_engine import MessageEngineError, call_groq
from app.services.outfit_service import (
    OutfitServiceError,
    generate_custom_outfit_for_customer,
    product_to_context,
)
from app.services.recommendation_engine import get_recommendations_for_customer
from app.utils.season_utils import (
    Hemisphere,
    Season,
    get_hemisphere,
    get_seasonal_style_keywords,
    season_to_display_name,
)

logger = logging.getLogger(__name__)


async def generate_seasonal_lookbook_for_customer(
    db: Session,
    store_id: int,
    customer_id: int,
    *,
    season: Optional[Season] = None,
    hemisphere: Optional[Hemisphere] = None,
    send_email: bool = True,
    recipient_email: Optional[str] = None,
    settings: Optional[AppSettings] = None,
) -> Optional[GeneratedOutfitImageResponse]:
    """
    Generate a seasonal lookbook from customer's existing wardrobe.

    Process:
    1. Fetch complete wardrobe memory
    2. Use FashionCLIP to find strongest outfit combination
    3. Check vector cache for existing image
    4. Generate image showing pieces styled 3 seasonal ways
    5. Write lookbook email (no selling, pure styling value)
    6. Include gap product recommendation
    7. Send at optimal time
    """
    settings = settings or load_settings()

    store = db.get(Store, store_id)
    if not store:
        raise OutfitServiceError(f"Store {store_id} not found", status_code=404)

    customer = db.get(Customer, customer_id)
    if not customer or customer.store_id != store_id:
        raise OutfitServiceError(f"Customer {customer_id} not found", status_code=404)

    memory = get_buyer_memory(db, store_id, customer_id)

    if not memory.wardrobe_items_json or len(memory.wardrobe_items_json) < 2:
        raise OutfitServiceError(
            f"Customer has insufficient wardrobe items ({len(memory.wardrobe_items_json or [])})",
            status_code=400,
        )

    if hemisphere is None:
        hemisphere = get_hemisphere(customer.country)

    if season is None:
        season = get_current_season(hemisphere=hemisphere)

    logger.info(
        f"Generating {season.value} lookbook for customer {customer_id} ({hemisphere.value})"
    )

    wardrobe_products = load_wardrobe_products(db, memory)

    if len(wardrobe_products) < 2:
        raise OutfitServiceError(
            "Insufficient products in wardrobe for outfit", status_code=400
        )

    best_combination = find_best_outfit_combination(
        db,
        wardrobe_products,
        customer=customer,
        season=season,
        limit=4,
    )

    if not best_combination:
        raise OutfitServiceError(
            "Could not find suitable outfit combination", status_code=400
        )

    product_context = [
        product_to_context(store, product, "owned") for product in best_combination
    ]

    gap_product = find_seasonal_gap_product(
        db, store, customer, memory, best_combination, season
    )
    if gap_product:
        product_context.append(product_to_context(store, gap_product, "seasonal_gap"))

    email_subject = generate_seasonal_subject(season, best_combination)
    email_body = generate_seasonal_email_body(
        settings=settings,
        customer=customer,
        memory=memory,
        products=best_combination,
        gap_product=gap_product,
        season=season,
    )

    image_prompt = generate_seasonal_image_prompt(
        products=best_combination,
        season=season,
        memory=memory,
    )

    trigger_reason = f"seasonal_lookbook_{season.value}"

    try:
        outfit = generate_custom_outfit_for_customer(
            db,
            store_id=store_id,
            customer_id=customer_id,
            order_id=None,
            product_context=product_context,
            trigger_reason=trigger_reason,
            prompt=image_prompt,
            email_subject=email_subject,
            email_body=email_body,
            send_email=send_email,
            recipient_email=recipient_email,
            settings=settings,
        )

        return outfit

    except Exception as e:
        logger.error(f"Failed to generate seasonal lookbook: {e}")
        raise


def load_wardrobe_products(db: Session, memory: BuyerMemory) -> list[Product]:
    """Load all products from customer's wardrobe."""
    if not memory.wardrobe_items_json:
        return []

    product_ids = [
        int(item["product_id"])
        for item in memory.wardrobe_items_json
        if item.get("product_id")
    ]

    if not product_ids:
        return []

    products = db.scalars(select(Product).where(Product.id.in_(product_ids))).all()

    product_map = {p.id: p for p in products}

    return [product_map[pid] for pid in product_ids if pid in product_map]


def find_best_outfit_combination(
    db: Session,
    wardrobe_products: list[Product],
    *,
    customer: Customer,
    season: Season,
    limit: int = 4,
) -> list[Product]:
    """
    Use FashionCLIP to find the strongest complete outfit combination.

    Strategy:
    1. Group products by category (top, bottom, outer, etc.)
    2. Score combinations by:
       - Category diversity (need top + bottom at minimum)
       - Style coherence via FashionCLIP
       - Seasonal relevance
       - Gender match
    3. Return top combination
    """
    customer_gender = get_customer_gender(db, customer)

    by_category = categorize_products(wardrobe_products)

    outfit = []

    tops = by_category.get("top", [])
    bottoms = by_category.get("bottom", [])
    outers = by_category.get("outer", [])
    accessories = by_category.get("accessories", [])

    if not tops or not bottoms:
        logger.warning(f"Customer {customer.id} lacks complete outfit components")
        return select_minimum_viable_outfit(wardrobe_products, limit)

    best_top = select_best_for_season(tops, season, customer_gender)
    best_bottom = select_best_for_season(bottoms, season, customer_gender)

    if best_top:
        outfit.append(best_top)
    if best_bottom:
        outfit.append(best_bottom)

    if outers and season in [Season.FALL, Season.WINTER]:
        best_outer = select_best_for_season(outers, season, customer_gender)
        if best_outer:
            outfit.append(best_outer)

    if outfit and len(outfit) < limit:
        clip = FashionClipService()

        outfit_signals = [
            ProductSignal(
                product_id=p.id,
                title=p.title,
                tags=p.tags,
                image_url=p.image_url,
            )
            for p in outfit
        ]

        remaining = [p for p in wardrobe_products if p not in outfit]
        remaining_scored = []

        for product in remaining:
            candidate_signal = ProductSignal(
                product_id=product.id,
                title=product.title,
                tags=product.tags,
                image_url=product.image_url,
            )

            coherence = max(
                clip.compatibility_score(anchor, candidate_signal)
                for anchor in outfit_signals
            )

            remaining_scored.append((coherence, product))

        remaining_scored.sort(key=lambda x: x[0], reverse=True)

        for score, product in remaining_scored[: limit - len(outfit)]:
            outfit.append(product)

    return outfit[:limit]


def categorize_products(products: list[Product]) -> dict[str, list[Product]]:
    """Categorize products by type (top, bottom, outer, accessories)."""
    categories = {
        "top": [],
        "bottom": [],
        "outer": [],
        "accessories": [],
        "other": [],
    }

    top_keywords = ["shirt", "tee", "t-shirt", "top", "blouse", "sweater", "hoodie"]
    bottom_keywords = ["jeans", "trousers", "pants", "shorts", "skirt", "cargos"]
    outer_keywords = ["jacket", "coat", "blazer", "cardigan"]
    accessory_keywords = ["accessories", "scarf", "hat", "belt"]

    for product in products:
        text = f"{product.title} {product.tags or ''}".lower()

        if any(kw in text for kw in top_keywords):
            categories["top"].append(product)
        elif any(kw in text for kw in bottom_keywords):
            categories["bottom"].append(product)
        elif any(kw in text for kw in outer_keywords):
            categories["outer"].append(product)
        elif any(kw in text for kw in accessory_keywords):
            categories["accessories"].append(product)
        else:
            categories["other"].append(product)

    return categories


def select_best_for_season(
    products: list[Product],
    season: Season,
    customer_gender: Optional[str],
) -> Optional[Product]:
    """Select the best product from a category for the given season."""
    if not products:
        return None

    scored = []
    seasonal_keywords = get_seasonal_style_keywords(season)

    for product in products:
        score = 0

        text = f"{product.title} {product.tags or ''}".lower()

        if customer_gender and customer_gender != "unisex":
            gender_tag = f"gender_{customer_gender}"
            if gender_tag in text:
                score += 10

        for keyword in seasonal_keywords:
            if keyword in text:
                score += 5

        scored.append((score, product))

    scored.sort(key=lambda x: x[0], reverse=True)

    return scored[0][1] if scored else products[0]


def select_minimum_viable_outfit(
    products: list[Product],
    limit: int,
) -> list[Product]:
    """Fallback: just return the most recent products."""
    return products[:limit]


def find_seasonal_gap_product(
    db: Session,
    store: Store,
    customer: Customer,
    memory: BuyerMemory,
    owned_products: list[Product],
    season: Season,
) -> Optional[Product]:
    """
    Find a product the customer doesn't own that would complement
    their wardrobe for this season.
    """
    owned_ids = {p.id for p in owned_products}

    recommendations = get_recommendations_for_customer(
        db,
        store_id=store.id,
        customer_id=customer.id,
        product_limit=10,
    )

    seasonal_keywords = get_seasonal_style_keywords(season)

    scored = []
    for rec in recommendations.recommendations:
        product = db.get(Product, rec.product_id)
        if not product or product.id in owned_ids:
            continue

        score = 0
        text = f"{product.title} {product.tags or ''}".lower()

        for keyword in seasonal_keywords:
            if keyword in text:
                score += 3

        scored.append((score, product))

    scored.sort(key=lambda x: x[0], reverse=True)

    return scored[0][1] if scored else None


def generate_seasonal_subject(season: Season, products: list[Product]) -> str:
    """Generate email subject line."""
    season_name = season_to_display_name(season)

    if products:
        return f"found a {season_name} outfit hiding in your wardrobe"

    return f"your {season_name} style, unlocked"


def generate_seasonal_email_body(
    *,
    settings: AppSettings,
    customer: Customer,
    memory: BuyerMemory,
    products: list[Product],
    gap_product: Optional[Product],
    season: Season,
) -> str:
    """
    Generate lookbook email body.

    Constraints:
    - Max 5 sentences introduction
    - No selling, no product links
    - GenZ casual tone
    - Acknowledge style aesthetic by name
    - Final line: gap product suggestion
    """
    season_name = season_to_display_name(season)
    product_titles = ", ".join(p.title for p in products[:3])
    style_aesthetic = memory.favorite_categories or "your style"

    gap_line = ""
    if gap_product:
        gap_line = f"\n\nmissing something for {season_name}? we might have it."

    prompt = (
        f"Write a seasonal lookbook email for a clothing brand customer.\n"
        f"They already own all the pieces shown: {product_titles}.\n"
        f"This is not about buying anything new.\n"
        f"Frame it as discovering outfits they already have.\n"
        f"Incoming season: {season_name}.\n"
        f"Their style aesthetic: {style_aesthetic}.\n"
        f"Acknowledge their specific style aesthetic by name.\n"
        f"Sound like a friend who found great combinations in their wardrobe.\n"
        f"GenZ tone. Max 5 sentences introduction.\n"
        f"No selling. No product links. Pure styling value.{gap_line}\n"
    )

    try:
        return call_groq(settings=settings, prompt=prompt)
    except MessageEngineError:
        name = customer.first_name or "there"
        fallback = (
            f"hey {name}, i found a {season_name} combo already sitting in your wardrobe. "
            f"it leans {style_aesthetic} with {memory.favorite_colors or 'your usual palette'}. "
            f"not selling you anything, just showing the pieces in a new way."
        )
        if gap_product:
            fallback += f" missing something for {season_name}? we might have it."
        return fallback


def generate_seasonal_image_prompt(
    products: list[Product],
    season: Season,
    memory: BuyerMemory,
) -> str:
    """
    Generate prompt for Seedream V4.

    Requirements:
    - ONE composed visual
    - Show owned pieces styled 3 different seasonal ways
    - No text, logos, labels
    - Natural model
    - Premium D2C campaign aesthetic
    """
    season_name = season_to_display_name(season)
    product_titles = ", ".join(p.title for p in products)
    seasonal_keywords = get_seasonal_style_keywords(season)

    return (
        f"Use the attached product reference images as visual anchors. "
        f"Match the exact colors, fabrics, and styles shown in those images. "
        f"One seasonal fashion lookbook image, exactly 3 styling options in one cohesive triptych. "
        f"Use only these already-owned wardrobe pieces: {product_titles}. "
        f"Season: {season_name}. Style keywords: {', '.join(seasonal_keywords[:5])}. "
        f"Show their best wardrobe combination styled three ways: "
        f"morning casual, afternoon outing, evening elevated. "
        f"Realistic premium D2C campaign, natural attractive model. "
        f"No text, no logos, no labels, no watermarks. "
        f"Customer style context: {memory.memory_summary or 'building their style identity'}."
    )


def get_current_season(hemisphere: Hemisphere) -> Season:
    """Import season helper to avoid circular dependency."""
    from app.utils.season_utils import get_current_season as _get_current_season

    return _get_current_season(hemisphere=hemisphere)
