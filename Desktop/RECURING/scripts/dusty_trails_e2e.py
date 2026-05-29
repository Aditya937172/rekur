from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import load_settings
from app.db.session import SessionLocal, init_db
from app.main import app
from app.models import (
    BuyerMemory,
    Customer,
    Event,
    GeneratedOutfitImage,
    Order,
    OrderItem,
    Product,
    Store,
    TrackingSession,
)
from app.schemas import GenerateOutfitImageRequest
from app.services.buyer_memory_service import (
    get_buyer_memory,
    update_buyer_memory_for_customer,
)
from app.services.image_generation_service import ImageGenerationResult
from app.services.outfit_service import (
    generate_outfit_for_customer,
    select_pairing_products,
    slugify,
)
from app.services.sync_service import update_customer_order_totals


STORE_NAME = "Dusty Trails Co."
STORE_DOMAIN = "dusty-trails-co.local.myshopify.com"
NANGO_CONNECTION_ID = "dusty-trails-local-e2e"
SEED_PREFIX = "dusty-trails"
REPORT_PATH = Path("data/dusty_trails_e2e_report.json")

TEST_EMAILS = [
    "adityasingh937172@gmauil.com",
    "adityapalghar@gmail.com",
    "adityasingh-extc@atharvacoe.ac.in",
]

ONE_PIXEL_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAFgwJ/lqVY5QAAAABJRU5ErkJggg=="
)


BASE_PRODUCTS: list[dict[str, Any]] = [
    {
        "title": "Classic Wrangler Denim Jeans",
        "tags": "bottoms,denim,western,americana,ranch,casual,gender_male,gender_unisex",
        "price": "75.00",
        "sizes": ["W28", "W30", "W32", "W34", "W36", "W38"],
        "colors": ["stonewash", "dark", "light"],
        "image": "https://images.unsplash.com/photo-1542272604-787c3835535d?auto=format&fit=crop&w=1200&q=80",
    },
    {
        "title": "Pearl Snap Flannel Shirt",
        "tags": "tops,flannel,plaid,western,country,ranch,gender_unisex",
        "price": "65.00",
        "sizes": ["XS", "S", "M", "L", "XL", "XXL"],
        "colors": ["red plaid", "blue plaid", "black"],
        "image": "https://images.unsplash.com/photo-1602810318383-e386cc2a3ccf?auto=format&fit=crop&w=1200&q=80",
    },
    {
        "title": "Embroidered Western Shirt",
        "tags": "tops,western,statement,country,americana,gender_unisex",
        "price": "85.00",
        "sizes": ["S", "M", "L", "XL"],
        "colors": ["ivory", "turquoise", "rust"],
        "image": "https://images.unsplash.com/photo-1596755094514-f87e34085b2c?auto=format&fit=crop&w=1200&q=80",
    },
    {
        "title": "Suede Fringe Jacket",
        "tags": "outerwear,western,statement,suede,fringe,country,gender_unisex",
        "price": "175.00",
        "sizes": ["XS", "S", "M", "L", "XL"],
        "colors": ["tan", "brown", "black"],
        "image": "https://images.unsplash.com/photo-1520975954732-35dd22299614?auto=format&fit=crop&w=1200&q=80",
        "zero_sizes": ["XS"],
    },
    {
        "title": "Classic Cowboy Boots",
        "tags": "footwear,western,boots,leather,country,americana,gender_unisex",
        "price": "215.00",
        "sizes": ["5", "6", "7", "8", "9", "10", "11", "12"],
        "colors": ["brown", "black", "cognac"],
        "image": "https://images.unsplash.com/photo-1608256246200-53e635b5b65f?auto=format&fit=crop&w=1200&q=80",
    },
    {
        "title": "Leather Belt with Buckle",
        "tags": "accessories,western,belt,leather,buckle,country,gender_unisex",
        "price": "55.00",
        "sizes": ["S", "M", "L"],
        "colors": ["brown", "black"],
        "image": "https://images.unsplash.com/photo-1624222247344-550fb60583dc?auto=format&fit=crop&w=1200&q=80",
    },
    {
        "title": "Wide Brim Felt Hat",
        "tags": "accessories,western,hat,felt,country,ranch,gender_unisex",
        "price": "70.00",
        "sizes": ["S", "M", "L"],
        "colors": ["tan", "black", "brown"],
        "image": "https://images.unsplash.com/photo-1529958030586-3aae4ca485ff?auto=format&fit=crop&w=1200&q=80",
    },
    {
        "title": "Bootcut Ranch Jeans",
        "tags": "bottoms,denim,western,bootcut,ranch,gender_unisex",
        "price": "82.00",
        "sizes": ["W28", "W30", "W32", "W34", "W36", "W38"],
        "colors": ["dark", "light"],
        "image": "https://images.unsplash.com/photo-1515886657613-9f3515b0c78f?auto=format&fit=crop&w=1200&q=80",
    },
    {
        "title": "Plaid Flannel Shacket",
        "tags": "outerwear,tops,flannel,plaid,western,layering,country,gender_unisex",
        "price": "98.00",
        "sizes": ["XS", "S", "M", "L", "XL"],
        "colors": ["green plaid", "brown plaid"],
        "image": "https://images.unsplash.com/photo-1520975682031-a616cf1feb4f?auto=format&fit=crop&w=1200&q=80",
    },
    {
        "title": "Denim Vest Jacket",
        "tags": "outerwear,denim,western,vest,americana,gender_unisex",
        "price": "85.00",
        "sizes": ["XS", "S", "M", "L", "XL"],
        "colors": ["light wash", "dark wash"],
        "image": "https://images.unsplash.com/photo-1576995853123-5a10305d93c0?auto=format&fit=crop&w=1200&q=80",
    },
    {
        "title": "Rodeo Graphic Tee",
        "tags": "tops,graphic,western,casual,rodeo,country,gender_unisex",
        "price": "40.00",
        "sizes": ["XS", "S", "M", "L", "XL", "XXL"],
        "colors": ["white", "black", "sand"],
        "image": "https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?auto=format&fit=crop&w=1200&q=80",
    },
    {
        "title": "Braided Leather Bracelet",
        "tags": "accessories,western,jewellery,leather,country,gender_unisex",
        "price": "30.00",
        "sizes": ["one size"],
        "colors": ["brown", "black"],
        "image": "https://images.unsplash.com/photo-1611591437281-460bfbe1220a?auto=format&fit=crop&w=1200&q=80",
    },
    {
        "title": "Canvas Ranch Tote",
        "tags": "accessories,bags,western,canvas,ranch,country,gender_unisex",
        "price": "65.00",
        "sizes": ["one size"],
        "colors": ["tan", "olive", "black"],
        "image": "https://images.unsplash.com/photo-1594223274512-ad4803739b7c?auto=format&fit=crop&w=1200&q=80",
    },
    {
        "title": "Turquoise Stone Necklace",
        "tags": "accessories,jewellery,western,turquoise,statement,gender_unisex",
        "price": "55.00",
        "sizes": ["one size"],
        "colors": ["turquoise"],
        "image": "https://images.unsplash.com/photo-1599643478518-a784e5dc4c8f?auto=format&fit=crop&w=1200&q=80",
    },
    {
        "title": "High Waist Denim Skirt",
        "tags": "bottoms,denim,western,skirt,country,gender_female",
        "price": "70.00",
        "sizes": ["XS", "S", "M", "L", "XL"],
        "colors": ["light", "dark"],
        "image": "https://images.unsplash.com/photo-1594633313593-bab3825d0caf?auto=format&fit=crop&w=1200&q=80",
    },
]

EXTRA_PRODUCTS: list[dict[str, Any]] = [
    ("Ranch Work Chore Coat", "outerwear,jacket,canvas,ranch,western,gender_unisex", "120.00"),
    ("Washed Denim Work Shirt", "tops,shirt,denim,western,ranch,gender_unisex", "72.00"),
    ("Prairie Cotton Midi Dress", "dress,western,prairie,country,gender_female", "118.00"),
    ("Canyon Ribbed Tank", "tops,tank,western,casual,gender_female", "38.00"),
    ("Dust Trail Cargo Pants", "bottoms,cargos,western,utility,gender_unisex", "88.00"),
    ("Rodeo Satin Bandana", "accessories,bandana,western,country,gender_unisex", "28.00"),
    ("Sherpa Lined Denim Jacket", "outerwear,jacket,denim,sherpa,western,gender_unisex", "138.00"),
    ("Raw Hem Denim Shorts", "bottoms,denim,shorts,western,gender_female", "58.00"),
    ("Ranch Check Flannel Overshirt", "tops,flannel,plaid,western,country,gender_unisex", "78.00"),
    ("Western Yoke Chambray Shirt", "tops,shirt,chambray,western,gender_unisex", "74.00"),
    ("Copper Desert Buckle Belt", "accessories,belt,western,statement,gender_unisex", "62.00"),
    ("Sunset Rodeo Hoodie", "tops,hoodie,western,graphic,casual,gender_unisex", "76.00"),
    ("Cattle Drive Trucker Jacket", "outerwear,jacket,western,denim,gender_unisex", "128.00"),
    ("Silver Star Boot Charm", "accessories,jewellery,western,boots,gender_unisex", "26.00"),
    ("Dakota Suede Mini Skirt", "bottoms,skirt,suede,western,gender_female", "78.00"),
    ("Rancher Henley Tee", "tops,tee,western,basics,gender_male,gender_unisex", "46.00"),
    ("Heritage Wool Poncho", "outerwear,poncho,western,statement,gender_unisex", "132.00"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed and test Dusty Trails Co. E2E.")
    parser.add_argument("--send-email", action="store_true", help="Send one sample outfit email.")
    parser.add_argument(
        "--recipient",
        default="adityapalghar@gmail.com",
        help="Recipient override for the sample email.",
    )
    parser.add_argument(
        "--mock-image",
        action="store_true",
        help="Use a local 1x1 image result instead of spending image API credits.",
    )
    parser.add_argument(
        "--skip-webhook",
        action="store_true",
        help="Skip the FastAPI webhook persistence test.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    init_db()
    if args.mock_image:
        install_mock_image_generator()

    db = SessionLocal()
    try:
        store = upsert_store(db)
        products = upsert_products(db, store)
        customers = upsert_customers(db, store)
        reset_tracking_events(db, store.id)
        cleanup_transient_orders(db, store.id)
        upsert_orders(db, store, products, customers)
        seed_tracking_events(db, store, products, customers)
        update_customer_order_totals(db, store.id)
        for customer in customers.values():
            update_buyer_memory_for_customer(db, store.id, customer.id)
        tune_edge_case_memories(db, store, customers)
        db.commit()

        report = build_report(db, store, products, customers, settings)
        report["webhook_test"] = (
            {"status": "skipped"} if args.skip_webhook else test_fulfilled_webhook(store)
        )
        report["edge_tests"] = run_edge_tests(db, store, products, customers)
        report["sample_outfit"] = generate_sample_outfit(
            db,
            store,
            products,
            customers,
            send_email=args.send_email,
            recipient=args.recipient,
            mock_image=args.mock_image,
        )
        write_report(report)
        print(json.dumps(report, indent=2, default=str))
    finally:
        db.close()


def install_mock_image_generator() -> None:
    import app.services.outfit_service as outfit_service

    def fake_generate_outfit_image(
        *,
        prompt: str,
        image_urls: list[str] | None = None,
        settings: Any | None = None,
    ) -> ImageGenerationResult:
        return ImageGenerationResult(
            task_id="dusty-trails-mock-image",
            task_status="completed",
            task_progress=100,
            image_url=None,
            image_base64=ONE_PIXEL_PNG,
            credits_reserved=0.0,
            credits_used=0.0,
            usage={"credits_reserved": 0.0, "credits_used": 0.0, "mock": True},
            raw_response={"status": "completed", "mock": True},
        )

    outfit_service.generate_outfit_image = fake_generate_outfit_image


def upsert_store(db: Session) -> Store:
    store = db.scalar(select(Store).where(Store.shopify_store_domain == STORE_DOMAIN))
    if not store:
        store = Store(
            name=STORE_NAME,
            nango_connection_id=NANGO_CONNECTION_ID,
            shopify_store_domain=STORE_DOMAIN,
        )
        db.add(store)
        db.flush()
    store.name = STORE_NAME
    store.nango_connection_id = NANGO_CONNECTION_ID
    return store


def all_product_specs() -> list[dict[str, Any]]:
    specs = [dict(product) for product in BASE_PRODUCTS]
    for title, tags, price in EXTRA_PRODUCTS:
        specs.append(
            {
                "title": title,
                "tags": tags,
                "price": price,
                "sizes": ["XS", "S", "M", "L", "XL"],
                "colors": ["tan", "brown", "black"],
                "image": "https://images.unsplash.com/photo-1515886657613-9f3515b0c78f?auto=format&fit=crop&w=1200&q=80",
            }
        )
    return specs


def upsert_products(db: Session, store: Store) -> dict[str, Product]:
    products: dict[str, Product] = {}
    for index, spec in enumerate(all_product_specs(), start=1):
        shopify_product_id = f"{SEED_PREFIX}-product-{index:03d}"
        product = db.scalar(
            select(Product).where(
                Product.store_id == store.id,
                Product.shopify_product_id == shopify_product_id,
            )
        )
        if not product:
            product = Product(
                store_id=store.id,
                shopify_product_id=shopify_product_id,
                title=spec["title"],
            )
            db.add(product)
            db.flush()
        tags = spec["tags"]
        if "seeded_by_retention_app" not in tags:
            tags = f"{tags},seeded_by_retention_app,western,country,americana"
        inventory = build_variant_inventory(spec, shopify_product_id)
        product.title = spec["title"]
        product.handle = slugify(spec["title"])
        product.description = (
            f"{spec['title']} from Dusty Trails Co., built for denim, boots, "
            "ranch weekends, and easy Americana styling."
        )
        product.price = Decimal(str(spec["price"]))
        product.image_url = spec["image"]
        product.tags = tags
        product.variant_inventory_json = inventory
        product.in_stock = any(item["available"] for item in inventory)
        products[product.title] = product
    db.flush()
    return products


def build_variant_inventory(spec: dict[str, Any], product_id: str) -> list[dict[str, Any]]:
    inventory = []
    zero_sizes = {size.lower() for size in spec.get("zero_sizes", [])}
    counter = 1
    for size in spec["sizes"]:
        for color in spec["colors"]:
            quantity = 0 if str(size).lower() in zero_sizes else (5 + counter % 11)
            inventory.append(
                {
                    "id": f"{product_id}-variant-{counter:03d}",
                    "title": f"{size} / {color}",
                    "sku": f"{product_id.upper()}-{counter:03d}",
                    "option_values": [str(size), str(color)],
                    "inventory_quantity": quantity,
                    "inventory_policy": "deny",
                    "inventory_management": "shopify",
                    "available": quantity > 0,
                }
            )
            counter += 1
    return inventory


def upsert_customers(db: Session, store: Store) -> dict[str, Customer]:
    customers: dict[str, Customer] = {}
    fixed = [
        ("boots_buyer", "Aarav", "Singh", "male", "adityapalghar@gmail.com"),
        ("accessory_buyer", "Mira", "Carter", "female", "adityasingh-extc@atharvacoe.ac.in"),
        ("xs_buyer", "Zoya", "Reed", "female", "adityapalghar@gmail.com"),
        ("plaid_repeat", "Noah", "Brooks", "male", "adityapalghar@gmail.com"),
    ]
    first_names = [
        "Avery",
        "Wyatt",
        "Harper",
        "Colton",
        "Sadie",
        "Logan",
        "Emery",
        "Beau",
        "Dakota",
        "Riley",
        "June",
        "Mason",
    ]
    last_names = [
        "Walker",
        "Hayes",
        "Cooper",
        "West",
        "Rivers",
        "Sutton",
        "Morgan",
        "Lane",
        "Carter",
        "Reed",
    ]
    for key, first, last, gender, email in fixed:
        customers[key] = upsert_customer(
            db,
            store,
            shopify_customer_id=f"{SEED_PREFIX}-{key}",
            first_name=first,
            last_name=last,
            gender=gender,
            email=email,
        )

    index = 1
    while len(customers) < 120:
        first = first_names[index % len(first_names)]
        last = last_names[(index * 3) % len(last_names)]
        gender = "female" if index % 2 else "male"
        email = f"dusty.customer.{index:03d}@example.com"
        key = f"customer_{index:03d}"
        customers[key] = upsert_customer(
            db,
            store,
            shopify_customer_id=f"{SEED_PREFIX}-customer-{index:03d}",
            first_name=first,
            last_name=last,
            gender=gender,
            email=email,
        )
        index += 1
    db.flush()
    return customers


def upsert_customer(
    db: Session,
    store: Store,
    *,
    shopify_customer_id: str,
    first_name: str,
    last_name: str,
    gender: str,
    email: str,
) -> Customer:
    customer = db.scalar(
        select(Customer).where(
            Customer.store_id == store.id,
            Customer.shopify_customer_id == shopify_customer_id,
        )
    )
    if not customer:
        customer = Customer(store_id=store.id, shopify_customer_id=shopify_customer_id)
        db.add(customer)
        db.flush()
    customer.first_name = first_name
    customer.last_name = last_name
    customer.gender = gender
    customer.email = email
    customer.city = "Austin" if gender == "male" else "Bozeman"
    customer.country = "United States"
    return customer


def upsert_orders(
    db: Session,
    store: Store,
    products: dict[str, Product],
    customers: dict[str, Customer],
) -> None:
    rng = random.Random(937172)
    now = utc_now()
    customer_list = list(customers.values())
    product_list = list(products.values())

    explicit_orders = [
        ("edge-boots", customers["boots_buyer"], ["Classic Cowboy Boots"], now - timedelta(days=3)),
        (
            "edge-accessory",
            customers["accessory_buyer"],
            ["Leather Belt with Buckle", "Braided Leather Bracelet"],
            now - timedelta(days=2),
        ),
        ("edge-xs", customers["xs_buyer"], ["Classic Cowboy Boots"], now - timedelta(days=1)),
        (
            "edge-plaid-1",
            customers["plaid_repeat"],
            ["Pearl Snap Flannel Shirt"],
            now - timedelta(days=150),
        ),
        (
            "edge-plaid-2",
            customers["plaid_repeat"],
            ["Plaid Flannel Shacket"],
            now - timedelta(days=90),
        ),
        (
            "edge-plaid-3",
            customers["plaid_repeat"],
            ["Ranch Check Flannel Overshirt"],
            now - timedelta(days=45),
        ),
        ("edge-plaid-boots", customers["plaid_repeat"], ["Classic Cowboy Boots"], now),
    ]
    for order_key, customer, titles, created_at in explicit_orders:
        upsert_order(
            db,
            store,
            shopify_order_id=f"{SEED_PREFIX}-order-{order_key}",
            customer=customer,
            products=[products[title] for title in titles],
            created_at=created_at,
        )

    for index in range(1, 234):
        customer = customer_list[index % len(customer_list)]
        item_count = 1 if index % 3 else 2
        chosen = rng.sample(product_list, item_count)
        created_at = now - timedelta(days=rng.randint(1, 365))
        upsert_order(
            db,
            store,
            shopify_order_id=f"{SEED_PREFIX}-order-{index:03d}",
            customer=customer,
            products=chosen,
            created_at=created_at,
        )
    db.flush()


def cleanup_transient_orders(db: Session, store_id: int) -> None:
    order_ids = [
        row[0]
        for row in db.execute(
            select(Order.id).where(
                Order.store_id == store_id,
                Order.shopify_order_id.like(f"{SEED_PREFIX}-webhook-%"),
            )
        ).all()
    ]
    if not order_ids:
        return
    db.execute(delete(OrderItem).where(OrderItem.order_id.in_(order_ids)))
    db.execute(delete(Order).where(Order.id.in_(order_ids)))
    db.flush()


def upsert_order(
    db: Session,
    store: Store,
    *,
    shopify_order_id: str,
    customer: Customer,
    products: list[Product],
    created_at: datetime,
) -> Order:
    order = db.scalar(
        select(Order).where(
            Order.store_id == store.id,
            Order.shopify_order_id == shopify_order_id,
        )
    )
    if not order:
        order = Order(store_id=store.id, shopify_order_id=shopify_order_id)
        db.add(order)
        db.flush()
    order.customer_id = customer.id
    order.currency = "USD"
    order.fulfillment_status = "delivered"
    order.delivered_at = created_at + timedelta(days=5)
    order.created_at = created_at
    order.items.clear()
    total = Decimal("0")
    for product in products:
        total += Decimal(product.price or 0)
        order.items.append(
            OrderItem(product_id=product.id, quantity=1, price=Decimal(product.price or 0))
        )
    order.total_price = total
    return order


def reset_tracking_events(db: Session, store_id: int) -> None:
    session_ids = [
        row[0]
        for row in db.execute(
            select(TrackingSession.id).where(
                TrackingSession.store_id == store_id,
                TrackingSession.session_id.like(f"{SEED_PREFIX}-%"),
            )
        ).all()
    ]
    if session_ids:
        db.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        db.execute(delete(TrackingSession).where(TrackingSession.id.in_(session_ids)))
    db.flush()


def seed_tracking_events(
    db: Session,
    store: Store,
    products: dict[str, Product],
    customers: dict[str, Customer],
) -> None:
    now = utc_now()
    scenarios = [
        (customers["boots_buyer"], products["Classic Cowboy Boots"], "mobile"),
        (customers["accessory_buyer"], products["Leather Belt with Buckle"], "desktop"),
        (customers["xs_buyer"], products["Suede Fringe Jacket"], "mobile"),
    ]
    for index, (customer, product, device) in enumerate(scenarios, start=1):
        session = TrackingSession(
            store_id=store.id,
            session_id=f"{SEED_PREFIX}-session-{index}",
            customer_id=customer.id,
            is_first_time=False,
            visit_count=2,
            started_at=now - timedelta(hours=index),
            last_seen_at=now,
        )
        db.add(session)
        db.flush()
        for view_index in range(3):
            db.add(
                Event(
                    store_id=store.id,
                    session_id=session.id,
                    customer_id=customer.id,
                    product_id=product.id,
                    event_type="product_view",
                    page_url=f"https://{STORE_DOMAIN}/products/{product.handle}",
                    referrer="https://google.com",
                    device_type=device,
                    time_spent=25000,
                    timestamp=now - timedelta(minutes=30 - view_index),
                    metadata_json={"source": "dusty_trails_e2e"},
                )
            )
        db.add(
            Event(
                store_id=store.id,
                session_id=session.id,
                customer_id=customer.id,
                product_id=product.id,
                event_type="add_to_cart",
                page_url=f"https://{STORE_DOMAIN}/products/{product.handle}",
                referrer="",
                device_type=device,
                time_spent=5000,
                timestamp=now,
                metadata_json={"source": "dusty_trails_e2e"},
            )
        )


def tune_edge_case_memories(
    db: Session,
    store: Store,
    customers: dict[str, Customer],
) -> None:
    xs_memory = get_buyer_memory(db, store.id, customers["xs_buyer"].id)
    xs_memory.style_tags = append_unique_csv(xs_memory.style_tags, "xs")
    xs_memory.memory_summary = f"{xs_memory.memory_summary or ''} Preferred size xs.".strip()
    db.flush()


def append_unique_csv(value: str | None, token: str) -> str:
    tokens = [item.strip() for item in (value or "").split(",") if item.strip()]
    if token not in {item.lower() for item in tokens}:
        tokens.append(token)
    return ", ".join(tokens)


def build_report(
    db: Session,
    store: Store,
    products: dict[str, Product],
    customers: dict[str, Customer],
    settings: Any,
) -> dict[str, Any]:
    product_count = db.scalar(select(func.count(Product.id)).where(Product.store_id == store.id))
    customer_count = db.scalar(select(func.count(Customer.id)).where(Customer.store_id == store.id))
    order_count = db.scalar(select(func.count(Order.id)).where(Order.store_id == store.id))
    event_count = db.scalar(select(func.count(Event.id)).where(Event.store_id == store.id))
    return {
        "store": {
            "id": store.id,
            "name": store.name,
            "domain": store.shopify_store_domain,
            "nango_connection_id": store.nango_connection_id,
        },
        "counts": {
            "products": product_count,
            "customers": customer_count,
            "orders": order_count,
            "events": event_count,
        },
        "catalog": {
            "expected_products": 32,
            "actual_products": len(products),
            "fringe_jacket_xs_available": variant_available(
                products["Suede Fringe Jacket"], "XS"
            ),
        },
        "email": {
            "configured_sender": settings.gmail_sender_email,
            "requested_test_addresses": TEST_EMAILS,
            "note": (
                "Gmail can only send from the authenticated mailbox or configured "
                "aliases. This script uses recipient override for testing."
            ),
        },
        "image": {
            "provider": settings.image_provider,
            "model": settings.image_model,
            "max_credits_per_task": settings.image_max_credits_per_task,
            "max_reference_urls": settings.image_max_reference_urls,
        },
    }


def test_fulfilled_webhook(store: Store) -> dict[str, Any]:
    import app.api.routes.webhooks as webhooks_route

    settings = load_settings()
    db = SessionLocal()
    try:
        product = db.scalar(
            select(Product).where(
                Product.store_id == store.id,
                Product.title == "Classic Cowboy Boots",
            )
        )
        customer = db.scalar(
            select(Customer).where(
                Customer.store_id == store.id,
                Customer.shopify_customer_id == f"{SEED_PREFIX}-boots_buyer",
            )
        )
        assert product is not None
        assert customer is not None
        payload = {
            "id": f"{SEED_PREFIX}-order-edge-boots",
            "created_at": utc_now().isoformat(),
            "updated_at": utc_now().isoformat(),
            "processed_at": utc_now().isoformat(),
            "currency": "USD",
            "total_price": str(product.price),
            "email": customer.email,
            "customer": {
                "id": customer.shopify_customer_id,
                "email": customer.email,
                "first_name": customer.first_name,
                "last_name": customer.last_name,
                "default_address": {
                    "city": customer.city,
                    "country": customer.country,
                    "phone": customer.phone,
                },
            },
            "line_items": [
                {
                    "product_id": product.shopify_product_id,
                    "title": product.title,
                    "name": product.title,
                    "quantity": 1,
                    "price": str(product.price),
                }
            ],
            "fulfillments": [{"updated_at": utc_now().isoformat()}],
        }
    finally:
        db.close()

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if settings.shopify_webhook_secret:
        digest = hmac.new(
            settings.shopify_webhook_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
        headers["X-Shopify-Hmac-Sha256"] = base64.b64encode(digest).decode("utf-8")

    original_queue = webhooks_route.queue_outfit_generation

    def fake_queue_outfit_generation(**_: Any) -> tuple[str, str]:
        return "test-noop", "dusty-trails-webhook-noop"

    webhooks_route.queue_outfit_generation = fake_queue_outfit_generation
    try:
        response = TestClient(app).post(
            f"/webhooks/shopify/{store.id}/orders-fulfilled",
            content=body,
            headers=headers,
        )
    finally:
        webhooks_route.queue_outfit_generation = original_queue

    result: dict[str, Any] = {
        "status_code": response.status_code,
        "response": safe_response_json(response),
    }
    db = SessionLocal()
    try:
        order = db.scalar(
            select(Order).where(
                Order.store_id == store.id,
                Order.shopify_order_id == payload["id"],
            )
        )
        result["persisted"] = bool(order)
        result["local_order_id"] = order.id if order else None
        result["fulfillment_status"] = order.fulfillment_status if order else None
    finally:
        db.close()
    return result


def run_edge_tests(
    db: Session,
    store: Store,
    products: dict[str, Product],
    customers: dict[str, Customer],
) -> list[dict[str, Any]]:
    tests = []
    tests.append(
        assert_pairing(
            name="boots_pair_with_jeans_and_shirt",
            db=db,
            store=store,
            customer=customers["boots_buyer"],
            purchased=[products["Classic Cowboy Boots"]],
            required_any=[["jeans"], ["shirt", "flannel"]],
        )
    )
    tests.append(
        assert_pairing(
            name="accessory_purchase_still_gets_wearable_outfit",
            db=db,
            store=store,
            customer=customers["accessory_buyer"],
            purchased=[products["Leather Belt with Buckle"]],
            required_any=[["jeans", "shirt", "jacket", "dress", "boots"]],
        )
    )
    tests.append(
        assert_pairing(
            name="xs_customer_does_not_get_out_of_stock_xs_fringe_jacket",
            db=db,
            store=store,
            customer=customers["xs_buyer"],
            purchased=[products["Classic Cowboy Boots"]],
            forbidden=["Suede Fringe Jacket"],
        )
    )
    tests.append(
        assert_pairing(
            name="repeat_plaid_buyer_does_not_get_fourth_plaid",
            db=db,
            store=store,
            customer=customers["plaid_repeat"],
            purchased=[products["Classic Cowboy Boots"]],
            forbidden_terms=["plaid", "flannel"],
        )
    )
    return tests


def assert_pairing(
    *,
    name: str,
    db: Session,
    store: Store,
    customer: Customer,
    purchased: list[Product],
    required_any: list[list[str]] | None = None,
    forbidden: list[str] | None = None,
    forbidden_terms: list[str] | None = None,
) -> dict[str, Any]:
    memory = get_buyer_memory(db, store.id, customer.id)
    pairings = select_pairing_products(
        db,
        store,
        memory,
        purchased,
        customer=customer,
        limit=3,
    )
    titles = [product.title for product in pairings]
    texts = [f"{product.title} {product.tags or ''}".lower() for product in pairings]
    passed = True
    failures: list[str] = []
    for group in required_any or []:
        if not any(any(term in text for term in group) for text in texts):
            passed = False
            failures.append(f"missing any of: {', '.join(group)}")
    for title in forbidden or []:
        if title in titles:
            passed = False
            failures.append(f"forbidden product selected: {title}")
    for term in forbidden_terms or []:
        if any(term in text for text in texts):
            passed = False
            failures.append(f"forbidden term selected: {term}")
    return {
        "name": name,
        "passed": passed,
        "customer_id": customer.id,
        "purchased": [product.title for product in purchased],
        "pairings": titles,
        "failures": failures,
    }


def generate_sample_outfit(
    db: Session,
    store: Store,
    products: dict[str, Product],
    customers: dict[str, Customer],
    *,
    send_email: bool,
    recipient: str,
    mock_image: bool,
) -> dict[str, Any]:
    customer = customers["boots_buyer"]
    order = db.scalar(
        select(Order).where(
            Order.store_id == store.id,
            Order.customer_id == customer.id,
            Order.shopify_order_id == f"{SEED_PREFIX}-order-edge-boots",
        )
    )
    if not order:
        return {"status": "skipped", "reason": "edge boots order missing"}
    try:
        response = generate_outfit_for_customer(
            db,
            store.id,
            GenerateOutfitImageRequest(
                customer_id=customer.id,
                order_id=order.id,
                trigger_reason="order_delivered_followup",
                send_email=send_email,
                recipient_email=recipient if send_email else None,
            ),
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        return {
            "status": "failed",
            "error": str(exc),
            "send_email": send_email,
            "mock_image": mock_image,
        }
    return {
        "status": response.status,
        "outfit_id": response.id,
        "order_id": response.order_id,
        "image_url": response.image_url,
        "credits_reserved": response.credits_reserved,
        "credits_used": response.credits_used,
        "sent_at": response.sent_at.isoformat() if response.sent_at else None,
        "recommended_products": [
            item.get("title")
            for item in response.recommended_products_json
            if item.get("role") == "recommended_pairing"
        ],
        "send_email": send_email,
        "recipient": recipient if send_email else None,
        "mock_image": mock_image,
    }


def variant_available(product: Product, size: str) -> bool:
    normalized_size = size.lower()
    inventory = product.variant_inventory_json or []
    matches = []
    for variant in inventory:
        if not isinstance(variant, dict):
            continue
        option_values = [
            str(value).lower() for value in variant.get("option_values") or []
        ]
        if normalized_size in option_values:
            matches.append(variant)
    return any(variant.get("available") for variant in matches)


def safe_response_json(response: Any) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text


def write_report(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


if __name__ == "__main__":
    main()
