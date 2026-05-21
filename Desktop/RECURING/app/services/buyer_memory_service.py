from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    BuyerMemory,
    Customer,
    Event,
    Order,
    Product,
    ReturnRefund,
    Store,
    TrackingSession,
)


class BuyerMemoryServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


COLOR_WORDS = {
    "black",
    "white",
    "blue",
    "navy",
    "green",
    "olive",
    "pink",
    "pastel",
    "red",
    "maroon",
    "beige",
    "cream",
    "grey",
    "gray",
    "brown",
    "yellow",
    "purple",
    "orange",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def rebuild_buyer_memory_for_store(db: Session, store_id: int) -> int:
    ensure_store_exists(db, store_id)
    customer_ids = [
        int(row[0])
        for row in db.execute(
            select(Customer.id).where(Customer.store_id == store_id)
        ).all()
    ]
    updated = 0
    for customer_id in customer_ids:
        update_buyer_memory_for_customer(db, store_id, customer_id)
        updated += 1
    db.flush()
    return updated


def get_buyer_memory(
    db: Session,
    store_id: int,
    customer_id: int,
) -> BuyerMemory:
    memory = db.scalar(
        select(BuyerMemory).where(
            BuyerMemory.store_id == store_id,
            BuyerMemory.customer_id == customer_id,
        )
    )
    if memory:
        return memory
    return update_buyer_memory_for_customer(db, store_id, customer_id)


def update_buyer_memory_for_customer(
    db: Session,
    store_id: int,
    customer_id: int,
) -> BuyerMemory:
    customer = db.get(Customer, customer_id)
    if not customer or customer.store_id != store_id:
        raise BuyerMemoryServiceError(
            f"Customer {customer_id} was not found.",
            status_code=404,
        )

    orders = db.scalars(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.store_id == store_id, Order.customer_id == customer_id)
        .order_by(Order.created_at.asc(), Order.id.asc())
    ).all()
    product_ids = {
        item.product_id
        for order in orders
        for item in order.items
        if item.product_id is not None
    }
    product_map = load_products(db, product_ids)

    order_history = build_order_history(orders, product_map)
    wardrobe_items = build_wardrobe_items(orders, product_map)
    recent_interests = load_recent_interests(db, store_id, customer_id)

    category_counts = Counter()
    color_counts = Counter()
    style_counts = Counter()
    for item in wardrobe_items + recent_interests:
        for tag in item.get("tags", []):
            normalized = normalize_tag(tag)
            if not normalized:
                continue
            if normalized in COLOR_WORDS:
                color_counts[normalized] += int(item.get("quantity") or 1)
            else:
                style_counts[normalized] += int(item.get("quantity") or 1)
        product_type = item.get("product_type") or item.get("category")
        if product_type:
            category_counts[normalize_tag(str(product_type))] += int(
                item.get("quantity") or 1
            )

    first_order = orders[0] if orders else None
    last_order = orders[-1] if orders else None
    order_total = sum((Decimal(order.total_price or 0) for order in orders), Decimal("0"))
    refunded_total = refunded_amount_for_customer(db, store_id, customer_id)
    total_spent = max(order_total - refunded_total, Decimal("0"))

    memory = db.scalar(
        select(BuyerMemory).where(
            BuyerMemory.store_id == store_id,
            BuyerMemory.customer_id == customer_id,
        )
    )
    if not memory:
        memory = BuyerMemory(store_id=store_id, customer_id=customer_id)
        db.add(memory)

    memory.first_order_at = first_order.created_at if first_order else None
    memory.last_order_at = last_order.created_at if last_order else None
    memory.last_order_id = last_order.id if last_order else None
    memory.total_orders = len(orders)
    memory.total_spent = total_spent
    memory.first_order_summary = order_summary(order_history[0]) if order_history else None
    memory.last_order_summary = order_summary(order_history[-1]) if order_history else None
    memory.wardrobe_summary = wardrobe_summary(wardrobe_items)
    memory.interest_summary = interest_summary(recent_interests)
    memory.favorite_categories = join_top(category_counts)
    memory.favorite_colors = join_top(color_counts)
    memory.style_tags = join_top(style_counts, limit=12)
    memory.price_band = price_band(total_spent, len(orders))
    memory.order_history_json = order_history
    memory.wardrobe_items_json = wardrobe_items
    memory.recent_interests_json = recent_interests
    memory.memory_summary = build_memory_summary(customer, memory)
    memory.updated_at = utc_now()
    db.flush()
    return memory


def build_order_history(
    orders: list[Order],
    product_map: dict[int, Product],
) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for order in orders:
        items = []
        for item in order.items:
            product = product_map.get(item.product_id or 0)
            items.append(
                {
                    "product_id": item.product_id,
                    "title": product.title if product else "Unknown product",
                    "quantity": item.quantity,
                    "price": float(item.price or 0),
                    "tags": parse_tags(product.tags if product else None),
                    "image_url": product.image_url if product else None,
                }
            )
        history.append(
            {
                "order_id": order.id,
                "shopify_order_id": order.shopify_order_id,
                "ordered_at": order.created_at.isoformat() if order.created_at else None,
                "total_price": float(order.total_price or 0),
                "currency": order.currency,
                "items": items,
            }
        )
    return history


def build_wardrobe_items(
    orders: list[Order],
    product_map: dict[int, Product],
) -> list[dict[str, Any]]:
    quantities: dict[int, int] = {}
    first_seen: dict[int, datetime | None] = {}
    last_seen: dict[int, datetime | None] = {}
    for order in orders:
        for item in order.items:
            if item.product_id is None:
                continue
            quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity
            first_seen.setdefault(item.product_id, order.created_at)
            last_seen[item.product_id] = order.created_at

    wardrobe: list[dict[str, Any]] = []
    for product_id, quantity in sorted(quantities.items()):
        product = product_map.get(product_id)
        if not product:
            continue
        tags = parse_tags(product.tags)
        wardrobe.append(
            {
                "product_id": product.id,
                "title": product.title,
                "quantity": quantity,
                "tags": tags,
                "category": infer_category(product),
                "colors": [tag for tag in tags if normalize_tag(tag) in COLOR_WORDS],
                "image_url": product.image_url,
                "first_ordered_at": first_seen[product_id].isoformat()
                if first_seen.get(product_id)
                else None,
                "last_ordered_at": last_seen[product_id].isoformat()
                if last_seen.get(product_id)
                else None,
            }
        )
    return wardrobe


def load_recent_interests(
    db: Session,
    store_id: int,
    customer_id: int,
    *,
    days: int = 30,
) -> list[dict[str, Any]]:
    cutoff = utc_now() - timedelta(days=days)
    event_customer_id = func.coalesce(Event.customer_id, TrackingSession.customer_id)
    rows = db.execute(
        select(
            Product,
            func.sum(case((Event.event_type == "product_view", 1), else_=0)).label(
                "views"
            ),
            func.sum(case((Event.event_type == "add_to_cart", 1), else_=0)).label(
                "carts"
            ),
            func.max(Event.timestamp).label("last_seen_at"),
        )
        .join(Event, Event.product_id == Product.id)
        .join(TrackingSession, TrackingSession.id == Event.session_id)
        .where(
            Event.store_id == store_id,
            Event.timestamp >= cutoff,
            event_customer_id == customer_id,
        )
        .group_by(Product.id)
        .order_by(func.max(Event.timestamp).desc())
        .limit(20)
    ).all()
    interests: list[dict[str, Any]] = []
    for product, views, carts, last_seen_at in rows:
        tags = parse_tags(product.tags)
        interests.append(
            {
                "product_id": product.id,
                "title": product.title,
                "views": int(views or 0),
                "added_to_cart": int(carts or 0),
                "tags": tags,
                "category": infer_category(product),
                "colors": [tag for tag in tags if normalize_tag(tag) in COLOR_WORDS],
                "image_url": product.image_url,
                "last_seen_at": last_seen_at.isoformat() if last_seen_at else None,
            }
        )
    return interests


def load_products(db: Session, product_ids: set[int | None]) -> dict[int, Product]:
    clean_ids = [product_id for product_id in product_ids if product_id is not None]
    if not clean_ids:
        return {}
    products = db.scalars(select(Product).where(Product.id.in_(clean_ids))).all()
    return {product.id: product for product in products}


def ensure_store_exists(db: Session, store_id: int) -> Store:
    store = db.get(Store, store_id)
    if not store:
        raise BuyerMemoryServiceError(f"Store {store_id} was not found.", status_code=404)
    return store


def parse_tags(value: str | None) -> list[str]:
    if not value:
        return []
    return [tag.strip() for tag in value.split(",") if tag.strip()]


def normalize_tag(value: str) -> str:
    return value.strip().lower().replace("_", " ").replace("-", " ")


def infer_category(product: Product) -> str | None:
    tags = [normalize_tag(tag) for tag in parse_tags(product.tags)]
    known = [
        "shirt",
        "t-shirt",
        "tee",
        "jeans",
        "trousers",
        "cargos",
        "dress",
        "hoodie",
        "jacket",
        "co-ord",
        "ethnic",
        "accessories",
    ]
    title = normalize_tag(product.title)
    for category in known:
        if category in tags or category in title:
            return category
    return tags[0] if tags else None


def order_summary(order: dict[str, Any]) -> str:
    titles = [item["title"] for item in order.get("items", []) if item.get("title")]
    date = order.get("ordered_at") or "unknown date"
    return f"Order on {date}: {', '.join(titles) if titles else 'no known products'}."


def wardrobe_summary(items: list[dict[str, Any]]) -> str:
    if not items:
        return "No purchase history yet."
    top_titles = [item["title"] for item in items[:8]]
    return f"Owns {len(items)} unique wardrobe items including {', '.join(top_titles)}."


def interest_summary(items: list[dict[str, Any]]) -> str:
    if not items:
        return "No recent product browsing signals."
    top_titles = [item["title"] for item in items[:6]]
    return f"Recently showed interest in {', '.join(top_titles)}."


def join_top(counter: Counter[str], *, limit: int = 8) -> str | None:
    values = [item for item, _ in counter.most_common(limit) if item]
    return ", ".join(values) if values else None


def price_band(total_spent: Decimal, total_orders: int) -> str:
    if total_orders <= 0:
        return "unknown"
    aov = total_spent / Decimal(total_orders)
    if aov >= Decimal("150"):
        return "premium"
    if aov >= Decimal("75"):
        return "mid"
    return "value"


def build_memory_summary(customer: Customer, memory: BuyerMemory) -> str:
    name = " ".join(
        part for part in [customer.first_name, customer.last_name] if part
    ).strip() or customer.email or f"Customer {customer.id}"
    pieces = [
        f"{name} has {memory.total_orders} orders and belongs to the {memory.price_band} price band.",
    ]
    if memory.favorite_categories:
        pieces.append(f"Favorite categories: {memory.favorite_categories}.")
    if memory.favorite_colors:
        pieces.append(f"Common colors: {memory.favorite_colors}.")
    if memory.style_tags:
        pieces.append(f"Style signals: {memory.style_tags}.")
    if memory.last_order_summary:
        pieces.append(f"Last order: {memory.last_order_summary}")
    if memory.interest_summary:
        pieces.append(memory.interest_summary)
    return " ".join(pieces)


def refunded_amount_for_customer(
    db: Session,
    store_id: int,
    customer_id: int,
) -> Decimal:
    refund_customer_id = func.coalesce(ReturnRefund.customer_id, Order.customer_id)
    total = db.scalar(
        select(func.coalesce(func.sum(ReturnRefund.amount), 0))
        .outerjoin(Order, ReturnRefund.order_id == Order.id)
        .where(
            ReturnRefund.store_id == store_id,
            refund_customer_id == customer_id,
        )
    )
    return Decimal(str(total or 0))
