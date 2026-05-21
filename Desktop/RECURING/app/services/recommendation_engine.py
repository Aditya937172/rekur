from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import (
    BuyerMemory,
    Customer,
    CustomerProfile,
    Event,
    Order,
    OrderItem,
    Product,
    Store,
    TrackingSession,
)
from app.schemas import CustomerRecommendations, ProductRecommendation
from app.services.intent_engine import get_customer_intents


class RecommendationEngineError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProductProfile:
    id: int
    shopify_product_id: str
    title: str
    tags: frozenset[str]
    keywords: frozenset[str]
    image_url: str | None
    price: Decimal | None


@dataclass
class ProductBehavior:
    product_id: int
    product_views: int = 0
    added_to_cart: int = 0
    last_event_at: datetime | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_recommendations_for_customers(
    db: Session,
    store_id: int,
    *,
    customer_limit: int = 5000,
    product_limit: int = 5,
) -> list[CustomerRecommendations]:
    ensure_store_exists(db, store_id)
    customer_ids = [
        row[0]
        for row in db.execute(
            select(Customer.id)
            .where(Customer.store_id == store_id)
            .order_by(Customer.id)
            .limit(customer_limit)
        ).all()
    ]
    return build_recommendations(
        db,
        store_id,
        customer_ids=customer_ids,
        product_limit=product_limit,
    )


def get_recommendations_for_customer(
    db: Session,
    store_id: int,
    customer_id: int,
    *,
    product_limit: int = 5,
) -> CustomerRecommendations:
    ensure_store_exists(db, store_id)
    customer = db.get(Customer, customer_id)
    if not customer or customer.store_id != store_id:
        raise RecommendationEngineError(f"Customer {customer_id} was not found.")
    results = build_recommendations(
        db,
        store_id,
        customer_ids=[customer_id],
        product_limit=product_limit,
    )
    return results[0]


def build_recommendations(
    db: Session,
    store_id: int,
    *,
    customer_ids: list[int],
    product_limit: int,
) -> list[CustomerRecommendations]:
    if not customer_ids:
        return []

    product_profiles = load_product_profiles(db, store_id)
    top_selling = load_top_selling_product_ids(db, store_id, limit=max(product_limit, 10))
    behavior_by_customer = load_recent_behavior(db, store_id, customer_ids)
    purchased_by_customer = load_purchased_product_ids(db, store_id, customer_ids)
    preference_terms_by_customer = load_customer_preference_terms(
        db,
        store_id,
        customer_ids,
    )
    intent_by_customer = {
        item.customer_id: item
        for item in get_customer_intents(
            db,
            store_id,
            limit=max(len(customer_ids), 1000),
        )
    }

    results: list[CustomerRecommendations] = []
    for customer_id in customer_ids:
        recommendations = recommend_for_customer(
            customer_id=customer_id,
            product_profiles=product_profiles,
            top_selling_product_ids=top_selling,
            behavior=behavior_by_customer.get(customer_id, {}),
            purchased_product_ids=purchased_by_customer.get(customer_id, set()),
            preference_terms=preference_terms_by_customer.get(customer_id, set()),
            limit=product_limit,
        )
        intent = intent_by_customer.get(customer_id)
        results.append(
            CustomerRecommendations(
                customer_id=customer_id,
                intent=intent.intent if intent else "low",
                score=intent.score if intent else 0,
                recommendations=recommendations,
            )
        )

    return sorted(
        results,
        key=lambda item: (item.score, len(item.recommendations)),
        reverse=True,
    )


def recommend_for_customer(
    *,
    customer_id: int,
    product_profiles: dict[int, ProductProfile],
    top_selling_product_ids: list[int],
    behavior: dict[int, ProductBehavior],
    purchased_product_ids: set[int],
    preference_terms: set[str],
    limit: int,
) -> list[ProductRecommendation]:
    recommendations: list[ProductRecommendation] = []
    seen_product_ids: set[int] = set()

    cart_candidates = sorted(
        (
            item
            for item in behavior.values()
            if item.added_to_cart > 0 and item.product_id not in purchased_product_ids
        ),
        key=lambda item: (item.added_to_cart, sortable_datetime(item.last_event_at)),
        reverse=True,
    )
    for item in cart_candidates:
        add_recommendation(
            recommendations,
            seen_product_ids,
            product_profiles.get(item.product_id),
            "Added to cart but not purchased",
            limit,
        )

    viewed_candidates = sorted(
        (item for item in behavior.values() if item.product_views >= 2),
        key=lambda item: (item.product_views, sortable_datetime(item.last_event_at)),
        reverse=True,
    )
    for item in viewed_candidates:
        add_recommendation(
            recommendations,
            seen_product_ids,
            product_profiles.get(item.product_id),
            "Viewed multiple times",
            limit,
        )

    for product_id in purchased_product_ids:
        purchased_profile = product_profiles.get(product_id)
        if not purchased_profile:
            continue
        for similar in find_similar_products(
            purchased_profile,
            product_profiles.values(),
            exclude_ids=purchased_product_ids | seen_product_ids,
        ):
            add_recommendation(
                recommendations,
                seen_product_ids,
                similar,
                "Similar to previous purchase",
                limit,
            )
            if len(recommendations) >= limit:
                break
        if len(recommendations) >= limit:
            break

    preference_candidates = find_preference_matching_products(
        preference_terms,
        product_profiles.values(),
        exclude_ids=purchased_product_ids | seen_product_ids,
    )
    for product in preference_candidates:
        add_recommendation(
            recommendations,
            seen_product_ids,
            product,
            "Matches stated style preference",
            limit,
        )
        if len(recommendations) >= limit:
            break

    for product_id in top_selling_product_ids:
        if product_id in purchased_product_ids:
            continue
        add_recommendation(
            recommendations,
            seen_product_ids,
            product_profiles.get(product_id),
            "Top-selling product",
            limit,
        )
        if len(recommendations) >= limit:
            break

    return recommendations


def load_customer_preference_terms(
    db: Session,
    store_id: int,
    customer_ids: list[int],
) -> dict[int, set[str]]:
    terms_by_customer: dict[int, set[str]] = defaultdict(set)
    memories = db.scalars(
        select(BuyerMemory).where(
            BuyerMemory.store_id == store_id,
            BuyerMemory.customer_id.in_(customer_ids),
        )
    ).all()
    for memory in memories:
        terms_by_customer[memory.customer_id].update(parse_freeform_terms(memory.style_tags))
        terms_by_customer[memory.customer_id].update(parse_freeform_terms(memory.favorite_colors))
        terms_by_customer[memory.customer_id].update(parse_freeform_terms(memory.interest_summary))

    profiles = db.scalars(
        select(CustomerProfile).where(
            CustomerProfile.store_id == store_id,
            CustomerProfile.customer_id.in_(customer_ids),
        )
    ).all()
    for profile in profiles:
        dimensions = profile.preference_dimensions_json or {}
        for key in [
            "mentioned_styles",
            "mentioned_colors",
            "wardrobe_gaps",
            "occasion_friction",
            "general_preferences",
        ]:
            values = dimensions.get(key) or []
            if isinstance(values, str):
                values = [values]
            for value in values:
                terms_by_customer[profile.customer_id].update(parse_freeform_terms(str(value)))
        for key in ["vibe_label", "style_orientation", "lifestyle"]:
            if dimensions.get(key):
                terms_by_customer[profile.customer_id].update(
                    parse_freeform_terms(str(dimensions[key]))
                )
        if profile.dominant_aesthetic:
            terms_by_customer[profile.customer_id].update(
                parse_freeform_terms(profile.dominant_aesthetic)
            )
        if profile.color_palette:
            terms_by_customer[profile.customer_id].update(
                parse_freeform_terms(profile.color_palette)
            )
    return terms_by_customer


def find_preference_matching_products(
    preference_terms: set[str],
    candidates: Iterable[ProductProfile],
    *,
    exclude_ids: set[int],
) -> list[ProductProfile]:
    if not preference_terms:
        return []
    scored: list[tuple[int, ProductProfile]] = []
    for candidate in candidates:
        if candidate.id in exclude_ids:
            continue
        product_terms = candidate.tags | candidate.keywords
        score = len(preference_terms & product_terms)
        if score > 0:
            scored.append((score, candidate))
    scored.sort(key=lambda item: (item[0], item[1].id), reverse=True)
    return [candidate for _, candidate in scored]


def load_recent_behavior(
    db: Session,
    store_id: int,
    customer_ids: list[int],
) -> dict[int, dict[int, ProductBehavior]]:
    cutoff = utc_now() - timedelta(days=7)
    event_customer_id = func.coalesce(Event.customer_id, TrackingSession.customer_id)
    rows = db.execute(
        select(
            event_customer_id.label("customer_id"),
            Event.product_id,
            func.sum(case((Event.event_type == "product_view", 1), else_=0)).label(
                "views"
            ),
            func.sum(case((Event.event_type == "add_to_cart", 1), else_=0)).label(
                "carts"
            ),
            func.max(Event.timestamp).label("last_event_at"),
        )
        .join(TrackingSession, TrackingSession.id == Event.session_id)
        .where(
            Event.store_id == store_id,
            Event.timestamp >= cutoff,
            Event.product_id.is_not(None),
            event_customer_id.in_(customer_ids),
        )
        .group_by(event_customer_id, Event.product_id)
    ).all()

    behavior: dict[int, dict[int, ProductBehavior]] = defaultdict(dict)
    for customer_id, product_id, views, carts, last_event_at in rows:
        behavior[int(customer_id)][int(product_id)] = ProductBehavior(
            product_id=int(product_id),
            product_views=int(views or 0),
            added_to_cart=int(carts or 0),
            last_event_at=last_event_at,
        )
    return behavior


def load_purchased_product_ids(
    db: Session,
    store_id: int,
    customer_ids: list[int],
) -> dict[int, set[int]]:
    rows = db.execute(
        select(Order.customer_id, OrderItem.product_id)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .where(
            Order.store_id == store_id,
            Order.customer_id.in_(customer_ids),
            OrderItem.product_id.is_not(None),
        )
        .group_by(Order.customer_id, OrderItem.product_id)
    ).all()
    purchased: dict[int, set[int]] = defaultdict(set)
    for customer_id, product_id in rows:
        purchased[int(customer_id)].add(int(product_id))
    return purchased


def load_top_selling_product_ids(
    db: Session,
    store_id: int,
    *,
    limit: int,
) -> list[int]:
    rows = db.execute(
        select(OrderItem.product_id, func.sum(OrderItem.quantity).label("units_sold"))
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.store_id == store_id, OrderItem.product_id.is_not(None))
        .group_by(OrderItem.product_id)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(limit)
    ).all()
    product_ids = [int(product_id) for product_id, _ in rows]
    if product_ids:
        return product_ids

    return [
        int(row[0])
        for row in db.execute(
            select(Product.id)
            .where(Product.store_id == store_id)
            .order_by(Product.updated_at.desc())
            .limit(limit)
        ).all()
    ]


def load_product_profiles(db: Session, store_id: int) -> dict[int, ProductProfile]:
    products = db.scalars(select(Product).where(Product.store_id == store_id)).all()
    return {
        product.id: ProductProfile(
            id=product.id,
            shopify_product_id=product.shopify_product_id,
            title=product.title,
            tags=parse_tags(product.tags),
            keywords=title_keywords(product.title),
            image_url=product.image_url,
            price=product.price,
        )
        for product in products
    }


def find_similar_products(
    source: ProductProfile,
    candidates: Iterable[ProductProfile],
    *,
    exclude_ids: set[int],
) -> list[ProductProfile]:
    scored: list[tuple[int, ProductProfile]] = []
    for candidate in candidates:
        if candidate.id == source.id or candidate.id in exclude_ids:
            continue
        tag_overlap = len(source.tags & candidate.tags)
        keyword_overlap = len(source.keywords & candidate.keywords)
        score = tag_overlap * 3 + keyword_overlap
        if score > 0:
            scored.append((score, candidate))

    scored.sort(key=lambda item: (item[0], item[1].id), reverse=True)
    return [candidate for _, candidate in scored]


def add_recommendation(
    recommendations: list[ProductRecommendation],
    seen_product_ids: set[int],
    product: ProductProfile | None,
    reason: str,
    limit: int,
) -> None:
    if len(recommendations) >= limit or not product or product.id in seen_product_ids:
        return
    seen_product_ids.add(product.id)
    recommendations.append(
        ProductRecommendation(
            product_id=product.id,
            shopify_product_id=product.shopify_product_id,
            title=product.title,
            reason=reason,
            image_url=product.image_url,
            price=float(product.price) if product.price is not None else None,
        )
    )


def ensure_store_exists(db: Session, store_id: int) -> Store:
    store = db.get(Store, store_id)
    if not store:
        raise RecommendationEngineError(f"Store {store_id} was not found.")
    return store


def parse_tags(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(
        tag.strip().lower()
        for tag in value.split(",")
        if tag and tag.strip()
    )


def parse_freeform_terms(value: str | None) -> set[str]:
    if not value:
        return set()
    stop_words = {
        "and",
        "the",
        "for",
        "with",
        "that",
        "this",
        "wants",
        "struggles",
        "style",
        "preference",
    }
    return {
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", value.lower())
        if token not in stop_words
    }


def title_keywords(title: str | None) -> frozenset[str]:
    if not title:
        return frozenset()
    stop_words = {"the", "and", "for", "with", "set", "fit", "new"}
    words = {
        word
        for word in re.findall(r"[a-z0-9]+", title.lower())
        if len(word) > 2 and word not in stop_words
    }
    return frozenset(words)


def sortable_datetime(value: datetime | None) -> float:
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()
