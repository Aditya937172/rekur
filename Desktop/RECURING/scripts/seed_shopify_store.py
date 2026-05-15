from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import AppSettings, load_settings
from app.services.shopify_client import ShopifyAPIError, ShopifyClient

try:
    from faker import Faker
except ImportError:  # pragma: no cover - fallback keeps dry runs usable.
    Faker = None


SEED_VERSION = "2026-04-retention-shopify-seed-v1"
SEED_TAG = "seeded_by_retention_app"
STATE_FILE_NAME = "shopify_seed_state.json"


COLOR_PALETTE = [
    ("Black", "black"),
    ("Ivory", "ivory"),
    ("White", "white"),
    ("Charcoal", "charcoal"),
    ("Navy", "navy"),
    ("Stone", "stone"),
    ("Olive", "olive"),
    ("Sage", "sage"),
    ("Blush", "blush"),
    ("Lilac", "lilac"),
    ("Powder Blue", "powder_blue"),
    ("Chocolate", "chocolate"),
    ("Maroon", "maroon"),
    ("Mustard", "mustard"),
    ("Indigo", "indigo"),
    ("Ecru", "ecru"),
]

STYLE_COLOR_PREFS = {
    "pastel_lover": ["blush", "lilac", "powder_blue", "sage", "ivory"],
    "black_neutral_basics": ["black", "charcoal", "white", "stone", "navy"],
    "formal_wear_buyer": ["white", "navy", "charcoal", "black", "ivory"],
    "streetwear_buyer": ["black", "olive", "charcoal", "indigo", "stone"],
    "ethnic_wear_buyer": ["maroon", "mustard", "indigo", "ivory", "sage"],
    "dress_buyer": ["blush", "black", "lilac", "powder_blue", "ivory"],
    "cargo_oversized_tee_buyer": ["olive", "black", "stone", "charcoal", "white"],
    "discount_only_buyer": ["black", "white", "navy", "olive", "stone"],
    "premium_buyer": ["black", "ivory", "chocolate", "navy", "charcoal"],
}

PRODUCT_CONFIGS = [
    {
        "category": "shirts",
        "product_type": "Shirts",
        "count": 16,
        "sizes": ["XS", "S", "M", "L", "XL", "XXL"],
        "price": (1299, 2699),
        "nouns": ["Oxford Shirt", "Resort Shirt", "Linen Shirt", "Poplin Shirt"],
        "adjectives": ["Relaxed", "Tailored", "Soft-Wash", "Everyday"],
        "clusters": ["formal_wear_buyer", "black_neutral_basics", "pastel_lover"],
        "occasions": ["work", "casual", "brunch", "travel"],
        "genders": ["men", "women", "unisex"],
    },
    {
        "category": "t-shirts",
        "product_type": "T-Shirts",
        "count": 18,
        "sizes": ["XS", "S", "M", "L", "XL", "XXL"],
        "price": (699, 1699),
        "nouns": ["Crew Tee", "Oversized Tee", "Rib Tee", "Graphic Tee"],
        "adjectives": ["Heavyweight", "Air-Knit", "Boxy", "Vintage-Wash"],
        "clusters": [
            "streetwear_buyer",
            "cargo_oversized_tee_buyer",
            "black_neutral_basics",
            "discount_only_buyer",
        ],
        "occasions": ["casual", "weekend", "travel", "college"],
        "genders": ["men", "women", "unisex"],
    },
    {
        "category": "jeans",
        "product_type": "Jeans",
        "count": 14,
        "sizes": ["26", "28", "30", "32", "34", "36", "38"],
        "price": (1799, 3499),
        "nouns": ["Straight Jeans", "Wide-Leg Jeans", "Slim Jeans", "Barrel Jeans"],
        "adjectives": ["Rigid", "Soft-Stretch", "Washed", "Clean-Fit"],
        "clusters": ["black_neutral_basics", "streetwear_buyer", "premium_buyer"],
        "occasions": ["casual", "weekend", "travel"],
        "genders": ["men", "women", "unisex"],
    },
    {
        "category": "trousers",
        "product_type": "Trousers",
        "count": 12,
        "sizes": ["26", "28", "30", "32", "34", "36", "38"],
        "price": (1499, 3299),
        "nouns": ["Pleated Trouser", "Cigarette Trouser", "Work Trouser"],
        "adjectives": ["Tailored", "Fluid", "Office-Ready", "Minimal"],
        "clusters": ["formal_wear_buyer", "premium_buyer", "black_neutral_basics"],
        "occasions": ["work", "formal", "dinner"],
        "genders": ["men", "women", "unisex"],
    },
    {
        "category": "cargos",
        "product_type": "Cargos",
        "count": 10,
        "sizes": ["26", "28", "30", "32", "34", "36", "38"],
        "price": (1599, 2999),
        "nouns": ["Cargo Pant", "Parachute Cargo", "Utility Jogger"],
        "adjectives": ["Utility", "Oversized", "Ripstop", "Street"],
        "clusters": ["streetwear_buyer", "cargo_oversized_tee_buyer"],
        "occasions": ["casual", "travel", "festival"],
        "genders": ["men", "women", "unisex"],
    },
    {
        "category": "dresses",
        "product_type": "Dresses",
        "count": 16,
        "sizes": ["XS", "S", "M", "L", "XL"],
        "price": (1699, 4999),
        "nouns": ["Midi Dress", "Slip Dress", "Shirt Dress", "Wrap Dress"],
        "adjectives": ["Draped", "Floral", "Satin", "Sunset"],
        "clusters": ["dress_buyer", "pastel_lover", "premium_buyer"],
        "occasions": ["brunch", "date-night", "vacation", "party"],
        "genders": ["women"],
    },
    {
        "category": "hoodies",
        "product_type": "Hoodies",
        "count": 12,
        "sizes": ["XS", "S", "M", "L", "XL", "XXL"],
        "price": (1499, 3299),
        "nouns": ["Pullover Hoodie", "Zip Hoodie", "Fleece Hoodie"],
        "adjectives": ["Cloud-Fleece", "Heavyweight", "Campus", "Oversized"],
        "clusters": ["streetwear_buyer", "cargo_oversized_tee_buyer"],
        "occasions": ["casual", "travel", "winter"],
        "genders": ["men", "women", "unisex"],
    },
    {
        "category": "jackets",
        "product_type": "Jackets",
        "count": 10,
        "sizes": ["XS", "S", "M", "L", "XL", "XXL"],
        "price": (2499, 6999),
        "nouns": ["Bomber Jacket", "Denim Jacket", "Shacket", "Quilted Jacket"],
        "adjectives": ["Layered", "Boxy", "Premium", "Weather-Ready"],
        "clusters": ["premium_buyer", "streetwear_buyer", "black_neutral_basics"],
        "occasions": ["winter", "travel", "night-out"],
        "genders": ["men", "women", "unisex"],
    },
    {
        "category": "co-ord sets",
        "product_type": "Co-ord Sets",
        "count": 12,
        "sizes": ["XS", "S", "M", "L", "XL"],
        "price": (2499, 5999),
        "nouns": ["Co-ord Set", "Resort Set", "Lounge Set"],
        "adjectives": ["Matching", "Textured", "Vacation", "Soft-Tailored"],
        "clusters": ["pastel_lover", "dress_buyer", "premium_buyer"],
        "occasions": ["vacation", "brunch", "party"],
        "genders": ["women", "unisex"],
    },
    {
        "category": "ethnic wear",
        "product_type": "Ethnic Wear",
        "count": 18,
        "sizes": ["XS", "S", "M", "L", "XL", "XXL"],
        "price": (1499, 7999),
        "nouns": ["Kurta Set", "A-Line Kurta", "Festive Jacket", "Palazzo Set"],
        "adjectives": ["Hand-Block", "Festive", "Embroidered", "Silk-Blend"],
        "clusters": ["ethnic_wear_buyer", "premium_buyer", "pastel_lover"],
        "occasions": ["festive", "wedding", "family-event", "work"],
        "genders": ["women", "men", "unisex"],
    },
    {
        "category": "accessories",
        "product_type": "Accessories",
        "count": 12,
        "sizes": ["One Size"],
        "price": (399, 2499),
        "nouns": ["Canvas Tote", "Leather Belt", "Silk Scarf", "Minimal Cap"],
        "adjectives": ["Everyday", "Premium", "Statement", "Gift-Ready"],
        "clusters": [
            "dress_buyer",
            "ethnic_wear_buyer",
            "premium_buyer",
            "black_neutral_basics",
        ],
        "occasions": ["casual", "work", "gift", "travel"],
        "genders": ["men", "women", "unisex"],
    },
]

IMAGE_POOLS = {
    "shirts": [
        "https://images.unsplash.com/photo-1602810318383-e386cc2a3ccf?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1598032895397-b9472444bf93?auto=format&fit=crop&w=1200&q=80",
    ],
    "t-shirts": [
        "https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1576566588028-4147f3842f27?auto=format&fit=crop&w=1200&q=80",
    ],
    "jeans": [
        "https://images.unsplash.com/photo-1542272604-787c3835535d?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1541099649105-f69ad21f3246?auto=format&fit=crop&w=1200&q=80",
    ],
    "trousers": [
        "https://images.unsplash.com/photo-1473966968600-fa801b869a1a?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1594633312681-425c7b97ccd1?auto=format&fit=crop&w=1200&q=80",
    ],
    "cargos": [
        "https://images.unsplash.com/photo-1515886657613-9f3515b0c78f?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1552374196-1ab2a1c593e8?auto=format&fit=crop&w=1200&q=80",
    ],
    "dresses": [
        "https://images.unsplash.com/photo-1529139574466-a303027c1d8b?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1539008835657-9e8e9680c956?auto=format&fit=crop&w=1200&q=80",
    ],
    "hoodies": [
        "https://images.unsplash.com/photo-1556821840-3a63f95609a7?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1578587018452-892bacefd3f2?auto=format&fit=crop&w=1200&q=80",
    ],
    "jackets": [
        "https://images.unsplash.com/photo-1543076447-215ad9ba6923?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1520975954732-35dd22299614?auto=format&fit=crop&w=1200&q=80",
    ],
    "co-ord sets": [
        "https://images.unsplash.com/photo-1539109136881-3be0616acf4b?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1515372039744-b8f02a3ae446?auto=format&fit=crop&w=1200&q=80",
    ],
    "ethnic wear": [
        "https://images.unsplash.com/photo-1583391733956-6c78276477e2?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1617627143750-d86bc21e42bb?auto=format&fit=crop&w=1200&q=80",
    ],
    "accessories": [
        "https://images.unsplash.com/photo-1506629905607-d405d7d3b0d2?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1523779105320-d1cd346ff52b?auto=format&fit=crop&w=1200&q=80",
    ],
}

CITY_PROFILES = [
    ("Mumbai", "Maharashtra", "400001"),
    ("Bengaluru", "Karnataka", "560001"),
    ("Delhi", "Delhi", "110001"),
    ("Hyderabad", "Telangana", "500001"),
    ("Chennai", "Tamil Nadu", "600001"),
    ("Pune", "Maharashtra", "411001"),
    ("Kolkata", "West Bengal", "700001"),
    ("Ahmedabad", "Gujarat", "380001"),
    ("Jaipur", "Rajasthan", "302001"),
    ("Kochi", "Kerala", "682001"),
    ("Chandigarh", "Chandigarh", "160017"),
    ("Gurugram", "Haryana", "122001"),
    ("Noida", "Uttar Pradesh", "201301"),
    ("Indore", "Madhya Pradesh", "452001"),
    ("Surat", "Gujarat", "395003"),
    ("Lucknow", "Uttar Pradesh", "226001"),
]

FIRST_NAMES = [
    "Aarav",
    "Aditi",
    "Aditya",
    "Ananya",
    "Arjun",
    "Diya",
    "Ira",
    "Kabir",
    "Kiara",
    "Meera",
    "Neha",
    "Nikhil",
    "Prisha",
    "Rohan",
    "Saanvi",
    "Sara",
    "Tara",
    "Vihaan",
    "Zara",
    "Ishaan",
]

LAST_NAMES = [
    "Agarwal",
    "Bansal",
    "Chopra",
    "Das",
    "Fernandes",
    "Gupta",
    "Iyer",
    "Jain",
    "Kapoor",
    "Khan",
    "Mehta",
    "Nair",
    "Patel",
    "Rao",
    "Shah",
    "Singh",
    "Verma",
    "Malhotra",
]

GROUP_PLAN = [
    ("A_high_intent", 140),
    ("B_medium_intent", 160),
    ("C_winback", 150),
    ("D_dead_suppress", 140),
    ("E_vip", 100),
    ("F_bad_data", 110),
    ("N_new_no_order", 100),
    ("O_discount_regular", 100),
]


def money(value: Decimal | int | float | str) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return re.sub(r"-+", "-", value).strip("-")


def tags_to_list(tags: Any) -> List[str]:
    if tags is None:
        return []
    if isinstance(tags, list):
        return [str(tag).strip() for tag in tags if str(tag).strip()]
    return [part.strip() for part in str(tags).split(",") if part.strip()]


def find_seed_tag_value(tags: Any, prefix: str) -> Optional[str]:
    for tag in tags_to_list(tags):
        if tag.startswith(prefix):
            return tag[len(prefix) :]
    return None


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def clean_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


class RetentionSeedGenerator:
    def __init__(self, seed: int = 20260425, today: Optional[datetime] = None) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.today = today or datetime.now(timezone.utc)
        self.fake = Faker("en_IN") if Faker else None
        if self.fake:
            self.fake.seed_instance(seed)

    def build(self) -> Dict[str, Any]:
        products = self.generate_products()
        customers = self.generate_customers()
        orders = self.generate_orders(customers, products)
        self.attach_customer_order_stats(customers, orders)
        recommendation_map = self.generate_recommendation_map(products)
        return {
            "products": products,
            "customers": customers,
            "orders": orders,
            "recommendation_map": recommendation_map,
        }

    def generate_products(self) -> List[Dict[str, Any]]:
        products: List[Dict[str, Any]] = []
        product_number = 1

        for config in PRODUCT_CONFIGS:
            for index in range(config["count"]):
                cluster = self.rng.choice(config["clusters"])
                colors = self.choose_colors(cluster, count=2 if index % 3 else 1)
                primary_color = colors[0]
                adjective = self.rng.choice(config["adjectives"])
                noun = self.rng.choice(config["nouns"])
                occasion = self.rng.choice(config["occasions"])
                gender = self.rng.choice(config["genders"])
                title = f"{primary_color[0]} {adjective} {noun}"
                handle = slugify(f"{title}-{product_number:03d}")
                price = self.round_price(self.rng.randint(*config["price"]))

                is_sale = product_number % 4 == 0 or self.rng.random() < 0.12
                is_bestseller = product_number % 7 == 0 or self.rng.random() < 0.08
                is_new = product_number % 5 == 0
                is_out = product_number % 19 == 0
                is_low = product_number % 11 == 0 and not is_out

                compare_at_price = None
                if is_sale:
                    compare_at_price = self.round_price(
                        int(Decimal(price) * Decimal(self.rng.choice(["1.20", "1.30", "1.40"])))
                    )

                tags = [
                    SEED_TAG,
                    f"seed_product_{handle}",
                    slugify(config["category"]),
                    slugify(config["product_type"]),
                    f"style_{cluster}",
                    f"occasion_{slugify(occasion)}",
                    f"gender_{gender}",
                    f"price_tier_{self.price_tier(price)}",
                ]
                tags.extend(f"color_{color_slug}" for _, color_slug in colors)
                if is_sale:
                    tags.extend(["sale", "discount_eligible"])
                if is_bestseller:
                    tags.append("bestseller")
                if is_new:
                    tags.append("new_arrival")
                if is_out:
                    tags.append("out_of_stock")
                if is_low:
                    tags.append("low_stock")

                variants = []
                for size in config["sizes"]:
                    for color_name, color_slug in colors:
                        inventory = self.inventory_quantity(is_out, is_low)
                        variants.append(
                            {
                                "size": size,
                                "color": color_name,
                                "sku": (
                                    f"AL-{slugify(config['category'])[:3].upper()}-"
                                    f"{product_number:03d}-{color_slug[:3].upper()}-"
                                    f"{slugify(size).upper()}"
                                ),
                                "price": money(price),
                                "compare_at_price": money(compare_at_price)
                                if compare_at_price
                                else None,
                                "inventory_quantity": inventory,
                                "inventory_policy": "deny",
                            }
                        )

                description = (
                    f"{title} from Aster & Loom, built for {occasion} styling. "
                    f"Made with a D2C wardrobe fit in mind, this {config['product_type'].lower()} "
                    f"pairs well with repeat-purchase recommendations and retention journeys."
                )
                image_pool = IMAGE_POOLS[config["category"]]
                image_urls = [
                    image_pool[product_number % len(image_pool)],
                    f"https://placehold.co/1200x1600/png?text={slugify(title)}",
                ]

                products.append(
                    {
                        "product_key": f"prod_{product_number:03d}",
                        "title": title,
                        "handle": handle,
                        "description": description,
                        "body_html": f"<p>{description}</p>",
                        "vendor": "Aster & Loom",
                        "product_type": config["product_type"],
                        "category": config["category"],
                        "style_cluster": cluster,
                        "occasion": occasion,
                        "gender": gender,
                        "base_price": money(price),
                        "compare_at_price": money(compare_at_price)
                        if compare_at_price
                        else None,
                        "tags": sorted(set(tags)),
                        "colors": [color_name for color_name, _ in colors],
                        "sizes": config["sizes"],
                        "variants": variants,
                        "image_urls": image_urls,
                        "flags": {
                            "sale": is_sale,
                            "bestseller": is_bestseller,
                            "new_arrival": is_new,
                            "out_of_stock": is_out,
                            "low_stock": is_low,
                        },
                    }
                )
                product_number += 1

        return products

    def generate_customers(self) -> List[Dict[str, Any]]:
        groups = [
            group for group, count in GROUP_PLAN for _ in range(count)
        ]
        self.rng.shuffle(groups)
        total = len(groups)
        keys = [f"cust_{index:04d}" for index in range(1, total + 1)]

        missing_email = set(self.rng.sample(keys, int(total * 0.05)))
        missing_phone = set(self.rng.sample(keys, int(total * 0.20)))
        malformed_candidates = [key for key in keys if key not in missing_phone]
        malformed_phone = set(self.rng.sample(malformed_candidates, int(total * 0.05)))
        incomplete_address = set(self.rng.sample(keys, int(total * 0.12)))
        duplicate_name_keys = set(self.rng.sample(keys, int(total * 0.06)))
        duplicate_names = [
            ("Riya", "Shah"),
            ("Aarav", "Mehta"),
            ("Neha", "Kapoor"),
            ("Kabir", "Singh"),
            ("Ananya", "Rao"),
        ]

        customers: List[Dict[str, Any]] = []
        group_seen: Counter[str] = Counter()

        for index, (customer_key, group) in enumerate(zip(keys, groups), start=1):
            group_seen[group] += 1
            first_name, last_name = self.name_for_customer(
                index, customer_key in duplicate_name_keys, duplicate_names
            )
            city, state, zipcode = self.rng.choice(CITY_PROFILES)
            style_cluster = self.style_cluster_for_group(group)
            planned_order_count = self.planned_order_count(group, group_seen[group])

            email = None
            if customer_key not in missing_email:
                email = (
                    f"{slugify(first_name)}.{slugify(last_name)}."
                    f"{index:04d}@example.com"
                )

            phone_valid = False
            raw_phone = None
            phone = None
            if customer_key not in missing_phone:
                if customer_key in malformed_phone:
                    raw_phone = self.rng.choice(
                        [
                            "99999",
                            "phone-missing-country-code",
                            "abcd-7788",
                            "+91 98XX invalid",
                            "0000000000",
                        ]
                    )
                    phone = raw_phone
                else:
                    phone = self.valid_indian_phone(index)
                    raw_phone = phone
                    phone_valid = True

            email_consent = self.email_consent(group, email is not None)
            sms_consent = self.sms_consent(group, phone_valid)
            address = self.address_for_customer(
                first_name,
                last_name,
                city,
                state,
                zipcode,
                phone if phone_valid else None,
                incomplete=customer_key in incomplete_address,
            )

            tags = [
                SEED_TAG,
                f"seed_customer_{customer_key}",
                f"style_{style_cluster}",
                f"group_{group.lower()}",
            ]
            tags.extend(self.group_tags(group))
            if planned_order_count == 0:
                tags.append("retention_no_orders")
            elif planned_order_count == 1:
                tags.append("retention_one_time_buyer")
            else:
                tags.append("retention_repeat_buyer")
            if email is None or not phone_valid or customer_key in incomplete_address:
                tags.append("retention_bad_data")
            if customer_key in duplicate_name_keys:
                tags.append("duplicate_looking_name")

            customers.append(
                {
                    "customer_key": customer_key,
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": email,
                    "phone": phone,
                    "phone_valid": phone_valid,
                    "raw_phone": raw_phone,
                    "city": city,
                    "state": state,
                    "country": "India",
                    "address": address,
                    "address_incomplete": customer_key in incomplete_address,
                    "email_marketing_consent": email_consent,
                    "sms_marketing_consent": sms_consent,
                    "scoring_group": group,
                    "style_cluster": style_cluster,
                    "planned_order_count": planned_order_count,
                    "tags": sorted(set(tags)),
                }
            )

        return customers

    def generate_orders(
        self, customers: List[Dict[str, Any]], products: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        products_by_cluster: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        products_by_category: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for product in products:
            products_by_cluster[product["style_cluster"]].append(product)
            products_by_category[product["category"]].append(product)

        orders: List[Dict[str, Any]] = []
        order_number = 1

        for customer in customers:
            count = customer["planned_order_count"]
            if count <= 0:
                continue
            dates = self.order_dates_for_group(customer["scoring_group"], count)
            for sequence, processed_at in enumerate(dates, start=1):
                line_items = self.order_line_items(
                    customer,
                    processed_at,
                    products,
                    products_by_cluster,
                    products_by_category,
                )
                subtotal = sum(
                    Decimal(item["price"]) * item["quantity"] for item in line_items
                )
                discount_rate = self.discount_rate_for_order(customer, line_items)
                discount_amount = (subtotal * discount_rate).quantize(Decimal("0.01"))
                shipping_price = Decimal("0.00") if subtotal >= 2499 else Decimal("99.00")
                total = max(Decimal("0.00"), subtotal - discount_amount + shipping_price)
                financial_status = self.financial_status_for_order(processed_at)
                fulfillment_status = self.fulfillment_status_for_order(
                    processed_at, financial_status
                )
                season_tag = self.season_tag(processed_at)
                order_key = f"order_{order_number:06d}"
                tags = [
                    SEED_TAG,
                    f"seed_order_{order_key}",
                    f"style_{customer['style_cluster']}",
                    season_tag,
                    "sale_purchase" if discount_rate > 0 else "full_price_purchase",
                    "high_value_order" if total >= Decimal("6500") else "standard_order",
                    f"financial_{financial_status}",
                ]
                if len(line_items) == 1:
                    tags.append("one_item_order")
                if customer["phone"] is None:
                    tags.append("missing_phone_order")
                if customer["email"] and not customer["phone_valid"]:
                    tags.append("email_no_valid_phone_order")

                shipping_address = None
                if self.rng.random() > 0.04 and not customer["address_incomplete"]:
                    shipping_address = customer["address"]

                orders.append(
                    {
                        "order_key": order_key,
                        "customer_key": customer["customer_key"],
                        "customer_email": customer["email"],
                        "processed_at": processed_at.isoformat().replace("+00:00", "Z"),
                        "line_items": line_items,
                        "subtotal_price": money(subtotal),
                        "discount_rate": money(discount_rate * Decimal("100")),
                        "discount_amount": money(discount_amount),
                        "shipping_price": money(shipping_price),
                        "total_price": money(total),
                        "discount_code": self.discount_code(customer, discount_rate),
                        "financial_status": financial_status,
                        "fulfillment_status": fulfillment_status,
                        "shipping_address": shipping_address,
                        "billing_address": customer["address"]
                        if self.rng.random() > 0.10
                        else None,
                        "tags": sorted(set(tags)),
                        "sequence_for_customer": sequence,
                    }
                )
                order_number += 1

        return orders

    def generate_recommendation_map(
        self, products: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        by_category: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        by_cluster: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for product in products:
            by_category[product["category"]].append(product)
            by_cluster[product["style_cluster"]].append(product)

        complementary_categories = {
            "shirts": ["trousers", "jeans", "accessories"],
            "t-shirts": ["cargos", "jeans", "jackets"],
            "jeans": ["shirts", "t-shirts", "hoodies"],
            "trousers": ["shirts", "jackets", "accessories"],
            "cargos": ["t-shirts", "hoodies", "jackets"],
            "dresses": ["accessories", "jackets"],
            "hoodies": ["cargos", "jeans", "t-shirts"],
            "jackets": ["t-shirts", "shirts", "dresses"],
            "co-ord sets": ["accessories", "jackets"],
            "ethnic wear": ["accessories", "co-ord sets"],
            "accessories": ["dresses", "shirts", "ethnic wear"],
        }
        seasonal_categories = {
            "shirts": ["trousers", "co-ord sets"],
            "t-shirts": ["cargos", "shorts", "accessories"],
            "jeans": ["jackets", "hoodies"],
            "trousers": ["shirts", "jackets"],
            "cargos": ["hoodies", "t-shirts"],
            "dresses": ["accessories", "jackets"],
            "hoodies": ["jackets", "cargos"],
            "jackets": ["hoodies", "jeans"],
            "co-ord sets": ["accessories", "dresses"],
            "ethnic wear": ["accessories", "jackets"],
            "accessories": ["dresses", "ethnic wear", "co-ord sets"],
        }

        recommendation_map: Dict[str, Dict[str, Any]] = {}
        for product in products:
            category = product["category"]
            handle = product["handle"]
            similar = [
                item
                for item in by_category[category]
                if item["handle"] != handle
                and item["style_cluster"] == product["style_cluster"]
            ]
            if len(similar) < 4:
                similar.extend(
                    item for item in by_category[category] if item["handle"] != handle
                )

            complementary_pool: List[Dict[str, Any]] = []
            for comp_category in complementary_categories.get(category, []):
                complementary_pool.extend(by_category.get(comp_category, []))

            seasonal_pool: List[Dict[str, Any]] = []
            for season_category in seasonal_categories.get(category, []):
                seasonal_pool.extend(by_category.get(season_category, []))

            base_price = Decimal(product["base_price"])
            upsell = [
                item
                for item in by_cluster[product["style_cluster"]]
                if item["handle"] != handle and Decimal(item["base_price"]) > base_price
            ]
            if len(upsell) < 4:
                upsell.extend(
                    item
                    for item in products
                    if item["handle"] != handle
                    and Decimal(item["base_price"]) > base_price
                    and "price_tier_premium" in item["tags"]
                )

            recommendation_map[handle] = {
                "product_title": product["title"],
                "category": category,
                "style_cluster": product["style_cluster"],
                "complementary_products": self.unique_handles(
                    complementary_pool, handle, 5
                ),
                "similar_products": self.unique_handles(similar, handle, 5),
                "upsell_products": self.unique_handles(upsell, handle, 5),
                "seasonal_products": self.unique_handles(seasonal_pool, handle, 5),
            }

        return recommendation_map

    def attach_customer_order_stats(
        self, customers: List[Dict[str, Any]], orders: List[Dict[str, Any]]
    ) -> None:
        order_count: Counter[str] = Counter()
        spend: Dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        last_order_at: Dict[str, datetime] = {}

        for order in orders:
            customer_key = order["customer_key"]
            order_count[customer_key] += 1
            if order["financial_status"] not in {"refunded", "voided"}:
                spend[customer_key] += Decimal(order["total_price"])
            processed_at = datetime.fromisoformat(
                order["processed_at"].replace("Z", "+00:00")
            )
            if (
                customer_key not in last_order_at
                or processed_at > last_order_at[customer_key]
            ):
                last_order_at[customer_key] = processed_at

        for customer in customers:
            customer_key = customer["customer_key"]
            customer["order_count"] = order_count[customer_key]
            customer["total_spent"] = money(spend[customer_key])
            if customer_key in last_order_at:
                last_at = last_order_at[customer_key]
                customer["last_order_at"] = last_at.isoformat().replace("+00:00", "Z")
                customer["days_since_last_order"] = (
                    self.today.date() - last_at.date()
                ).days
            else:
                customer["last_order_at"] = None
                customer["days_since_last_order"] = None

    def choose_colors(self, style_cluster: str, count: int) -> List[Tuple[str, str]]:
        preferred = STYLE_COLOR_PREFS.get(style_cluster, [])
        by_slug = {slug: (name, slug) for name, slug in COLOR_PALETTE}
        choices = [by_slug[slug] for slug in preferred if slug in by_slug]
        choices.extend(color for color in COLOR_PALETTE if color not in choices)
        self.rng.shuffle(choices)
        return choices[:count]

    def round_price(self, value: int) -> Decimal:
        rounded = int(round(value / 50) * 50)
        return Decimal(rounded)

    @staticmethod
    def price_tier(price: Decimal) -> str:
        if price >= Decimal("4499"):
            return "premium"
        if price <= Decimal("999"):
            return "value"
        return "core"

    def inventory_quantity(self, is_out: bool, is_low: bool) -> int:
        if is_out:
            return 0
        if is_low:
            return self.rng.randint(1, 3)
        return self.rng.randint(8, 80)

    def name_for_customer(
        self,
        index: int,
        duplicate_like: bool,
        duplicate_names: List[Tuple[str, str]],
    ) -> Tuple[str, str]:
        if duplicate_like:
            return duplicate_names[index % len(duplicate_names)]
        if self.fake:
            first_name = self.fake.first_name()
            last_name = self.fake.last_name()
            return first_name, last_name
        return (
            self.rng.choice(FIRST_NAMES),
            self.rng.choice(LAST_NAMES),
        )

    def style_cluster_for_group(self, group: str) -> str:
        if group == "E_vip":
            return self.rng.choice(["premium_buyer", "formal_wear_buyer", "dress_buyer"])
        if group == "O_discount_regular":
            return self.rng.choice(["discount_only_buyer", "black_neutral_basics"])
        if group == "D_dead_suppress":
            return self.rng.choice(["discount_only_buyer", "ethnic_wear_buyer"])
        if group == "F_bad_data":
            return self.rng.choice(
                ["streetwear_buyer", "cargo_oversized_tee_buyer", "pastel_lover"]
            )
        return self.rng.choice(list(STYLE_COLOR_PREFS.keys()))

    def planned_order_count(self, group: str, group_index: int) -> int:
        if group == "N_new_no_order":
            return 0
        if group == "E_vip":
            return self.rng.randint(5, 12)
        if group == "A_high_intent":
            return self.rng.randint(2, 5)
        if group == "B_medium_intent":
            return 1 if group_index <= 110 else 2
        if group == "C_winback":
            return 1 if group_index <= 80 else self.rng.randint(2, 3)
        if group == "D_dead_suppress":
            return 1 if group_index <= 50 else 2
        if group == "F_bad_data":
            return 1 if group_index <= 10 else 2
        return self.rng.randint(2, 4)

    @staticmethod
    def group_tags(group: str) -> List[str]:
        mapping = {
            "A_high_intent": ["retention_high_intent"],
            "B_medium_intent": ["retention_medium_intent"],
            "C_winback": ["retention_winback"],
            "D_dead_suppress": ["retention_dead", "retention_winback"],
            "E_vip": ["retention_vip", "retention_high_intent"],
            "F_bad_data": ["retention_bad_data"],
            "N_new_no_order": ["retention_new_customer"],
            "O_discount_regular": ["retention_discount_buyer"],
        }
        return mapping[group]

    def valid_indian_phone(self, index: int) -> str:
        prefix = self.rng.choice(["6", "7", "8", "9"])
        return f"+91{prefix}{index:09d}"[-13:]

    def address_for_customer(
        self,
        first_name: str,
        last_name: str,
        city: str,
        state: str,
        zipcode: str,
        phone: Optional[str],
        *,
        incomplete: bool,
    ) -> Dict[str, Any]:
        if self.fake:
            street = self.fake.street_address()
        else:
            street = f"{self.rng.randint(11, 998)}, {self.rng.choice(['Market Road', 'Lake View', 'MG Road', 'Park Street'])}"

        return clean_dict(
            {
                "first_name": first_name,
                "last_name": last_name,
                "address1": None if incomplete and self.rng.random() < 0.50 else street,
                "address2": None
                if incomplete or self.rng.random() < 0.60
                else f"Apt {self.rng.randint(101, 2404)}",
                "city": city,
                "province": state,
                "country": "India",
                "zip": None if incomplete and self.rng.random() < 0.40 else zipcode,
                "phone": phone,
            }
        )

    def email_consent(self, group: str, has_email: bool) -> bool:
        if not has_email:
            return False
        if group == "D_dead_suppress":
            return self.rng.random() < 0.18
        if group == "F_bad_data":
            return self.rng.random() < 0.45
        if group == "E_vip":
            return self.rng.random() < 0.82
        return self.rng.random() < 0.68

    def sms_consent(self, group: str, phone_valid: bool) -> bool:
        if not phone_valid:
            return False
        if group == "D_dead_suppress":
            return self.rng.random() < 0.12
        if group == "E_vip":
            return self.rng.random() < 0.62
        return self.rng.random() < 0.44

    def order_dates_for_group(self, group: str, count: int) -> List[datetime]:
        ranges = {
            "A_high_intent": (0, 7),
            "B_medium_intent": (30, 60),
            "C_winback": (90, 180),
            "D_dead_suppress": (240, 365),
            "E_vip": (0, 45),
            "F_bad_data": (20, 300),
            "O_discount_regular": (10, 220),
            "N_new_no_order": (0, 0),
        }
        latest_start, latest_end = ranges[group]
        latest_days_ago = self.rng.randint(latest_start, latest_end)
        dates = [self.datetime_days_ago(latest_days_ago)]

        for _ in range(count - 1):
            older_min = min(365, latest_days_ago + 14)
            older_max = 365
            if group == "E_vip":
                older_min = min(365, latest_days_ago + 7)
            older_days_ago = self.rng.randint(older_min, older_max)
            dates.append(self.datetime_days_ago(older_days_ago))

        return sorted(dates)

    def datetime_days_ago(self, days_ago: int) -> datetime:
        base = self.today - timedelta(days=days_ago)
        return base.replace(
            hour=self.rng.randint(8, 22),
            minute=self.rng.randint(0, 59),
            second=self.rng.randint(0, 59),
            microsecond=0,
        )

    def order_line_items(
        self,
        customer: Dict[str, Any],
        processed_at: datetime,
        products: List[Dict[str, Any]],
        products_by_cluster: Dict[str, List[Dict[str, Any]]],
        products_by_category: Dict[str, List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        item_count = self.rng.choices([1, 2, 3, 4], weights=[54, 31, 12, 3], k=1)[0]
        candidates = list(products_by_cluster.get(customer["style_cluster"], []))
        seasonal = self.preferred_categories_for_month(processed_at.month)
        seasonal_candidates = [
            product
            for category in seasonal
            for product in products_by_category.get(category, [])
            if product["style_cluster"] == customer["style_cluster"]
        ]
        if seasonal_candidates and self.rng.random() < 0.55:
            candidates = seasonal_candidates + candidates
        if customer["style_cluster"] == "discount_only_buyer":
            sale_candidates = [product for product in products if product["flags"]["sale"]]
            candidates = sale_candidates + candidates
        if customer["style_cluster"] == "premium_buyer":
            premium_candidates = [
                product for product in products if "price_tier_premium" in product["tags"]
            ]
            candidates = premium_candidates + candidates
        if not candidates:
            candidates = products

        selected: List[Dict[str, Any]] = []
        seen_handles = set()
        while len(selected) < item_count:
            product = self.rng.choice(candidates)
            if product["handle"] in seen_handles and len(seen_handles) < len(candidates):
                continue
            seen_handles.add(product["handle"])
            selected.append(product)

        items = []
        for product in selected:
            variant = self.rng.choice(product["variants"])
            quantity = 2 if self.rng.random() < 0.08 else 1
            items.append(
                {
                    "product_key": product["product_key"],
                    "product_handle": product["handle"],
                    "product_title": product["title"],
                    "variant_sku": variant["sku"],
                    "variant_title": f"{variant['size']} / {variant['color']}",
                    "category": product["category"],
                    "style_cluster": product["style_cluster"],
                    "is_sale_product": product["flags"]["sale"],
                    "quantity": quantity,
                    "price": variant["price"],
                }
            )
        return items

    @staticmethod
    def preferred_categories_for_month(month: int) -> List[str]:
        if month in {11, 12, 1, 2}:
            return ["hoodies", "jackets", "jeans", "ethnic wear"]
        if month in {3, 4, 5, 6}:
            return ["t-shirts", "shirts", "dresses", "co-ord sets"]
        if month in {8, 9, 10}:
            return ["ethnic wear", "accessories", "dresses"]
        return ["shirts", "trousers", "cargos", "accessories"]

    def discount_rate_for_order(
        self, customer: Dict[str, Any], line_items: List[Dict[str, Any]]
    ) -> Decimal:
        has_sale_item = any(item.get("is_sale_product") for item in line_items)
        if customer["style_cluster"] == "discount_only_buyer":
            return Decimal(self.rng.choice(["0.10", "0.15", "0.20"]))
        if has_sale_item or self.rng.random() < 0.22:
            return Decimal(self.rng.choice(["0.05", "0.10", "0.15"]))
        return Decimal("0.00")

    def discount_code(
        self, customer: Dict[str, Any], discount_rate: Decimal
    ) -> Optional[str]:
        if discount_rate <= 0:
            return None
        if customer["style_cluster"] == "discount_only_buyer":
            return self.rng.choice(["SALE20", "EXTRA15", "APPONLY10"])
        return self.rng.choice(["WELCOME10", "STYLE10", "RETENTION15"])

    def financial_status_for_order(self, processed_at: datetime) -> str:
        age_days = (self.today.date() - processed_at.date()).days
        roll = self.rng.random()
        if age_days > 30 and roll < 0.025:
            return "refunded"
        if age_days > 30 and roll < 0.045:
            return "voided"
        if age_days <= 5 and roll < 0.08:
            return "pending"
        return "paid"

    def fulfillment_status_for_order(
        self, processed_at: datetime, financial_status: str
    ) -> Optional[str]:
        if financial_status in {"refunded", "voided", "pending"}:
            return None
        age_days = (self.today.date() - processed_at.date()).days
        if age_days <= 3:
            return None
        return "partial" if self.rng.random() < 0.06 else "fulfilled"

    @staticmethod
    def season_tag(processed_at: datetime) -> str:
        month = processed_at.month
        if month in {11, 12, 1, 2}:
            return "season_winter"
        if month in {3, 4, 5, 6}:
            return "season_summer"
        if month in {8, 9, 10}:
            return "season_festive"
        return "season_monsoon"

    def unique_handles(
        self, products: Iterable[Dict[str, Any]], current_handle: str, limit: int
    ) -> List[str]:
        handles = []
        seen = {current_handle}
        pool = list(products)
        self.rng.shuffle(pool)
        for product in pool:
            handle = product["handle"]
            if handle in seen:
                continue
            seen.add(handle)
            handles.append(handle)
            if len(handles) == limit:
                break
        return handles


def build_channel_readiness(customer: Dict[str, Any]) -> Dict[str, Any]:
    can_email = bool(customer.get("email") and customer["email_marketing_consent"])
    if can_email:
        cannot_email_reason = None
    elif not customer.get("email"):
        cannot_email_reason = "missing_email"
    else:
        cannot_email_reason = "no_email_marketing_consent"

    can_whatsapp = bool(
        customer.get("phone")
        and customer.get("phone_valid")
        and customer["sms_marketing_consent"]
    )
    if can_whatsapp:
        cannot_whatsapp_reason = None
    elif not customer.get("phone"):
        cannot_whatsapp_reason = "missing_phone"
    elif not customer.get("phone_valid"):
        cannot_whatsapp_reason = "invalid_phone_format"
    else:
        cannot_whatsapp_reason = "no_sms_whatsapp_consent"

    if can_email and can_whatsapp:
        recommended_channel = "both"
    elif can_email:
        recommended_channel = "email"
    elif can_whatsapp:
        recommended_channel = "whatsapp"
    else:
        recommended_channel = "suppress"

    return {
        "customer_key": customer["customer_key"],
        "email": customer.get("email"),
        "phone": customer.get("phone"),
        "can_email": can_email,
        "cannot_email_reason": cannot_email_reason,
        "can_whatsapp": can_whatsapp,
        "cannot_whatsapp_reason": cannot_whatsapp_reason,
        "recommended_channel": recommended_channel,
        "scoring_group": customer["scoring_group"],
        "style_cluster": customer["style_cluster"],
        "tags": customer["tags"],
    }


def build_seed_report(
    dataset: Dict[str, Any],
    settings: AppSettings,
    operation_stats: Dict[str, Any],
) -> Dict[str, Any]:
    customers = dataset["customers"]
    products = dataset["products"]
    orders = dataset["orders"]
    readiness = [build_channel_readiness(customer) for customer in customers]
    readiness_by_key = {item["customer_key"]: item for item in readiness}

    product_quantity: Counter[str] = Counter()
    product_revenue: Dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    city_count: Counter[str] = Counter()
    cluster_count: Counter[str] = Counter()
    net_revenue = Decimal("0.00")
    gross_revenue = Decimal("0.00")

    customers_by_key = {customer["customer_key"]: customer for customer in customers}
    products_by_handle = {product["handle"]: product for product in products}

    for customer in customers:
        city_count[customer["city"]] += 1
        cluster_count[customer["style_cluster"]] += 1

    for order in orders:
        order_total = Decimal(order["total_price"])
        gross_revenue += order_total
        if order["financial_status"] not in {"refunded", "voided"}:
            net_revenue += order_total
        for item in order["line_items"]:
            product_quantity[item["product_handle"]] += item["quantity"]
            product_revenue[item["product_handle"]] += (
                Decimal(item["price"]) * item["quantity"]
            )

    top_products = []
    for handle, quantity in product_quantity.most_common(15):
        product = products_by_handle[handle]
        top_products.append(
            {
                "handle": handle,
                "title": product["title"],
                "quantity": quantity,
                "gross_merchandise_value": money(product_revenue[handle]),
                "style_cluster": product["style_cluster"],
            }
        )

    average_order_value = (
        money(net_revenue / len(orders)) if orders else money(Decimal("0.00"))
    )
    no_orders_count = sum(1 for customer in customers if customer["order_count"] == 0)
    one_time_count = sum(1 for customer in customers if customer["order_count"] == 1)
    repeat_count = sum(1 for customer in customers if customer["order_count"] >= 2)
    inactive_count = sum(
        1
        for customer in customers
        if customer["days_since_last_order"] is not None
        and customer["days_since_last_order"] >= 120
    )

    group_counts = Counter(customer["scoring_group"] for customer in customers)
    tag_counts = Counter(tag for customer in customers for tag in customer["tags"])
    recommended_channel_counts = Counter(
        item["recommended_channel"] for item in readiness
    )

    sample_keys = [
        "cust_0001",
        "cust_0010",
        "cust_0100",
        "cust_0250",
        "cust_0500",
        "cust_0750",
        "cust_1000",
    ]
    sample_customer_profiles = []
    for key in sample_keys:
        customer = customers_by_key.get(key)
        if not customer:
            continue
        sample_customer_profiles.append(
            {
                "customer_key": key,
                "name": f"{customer['first_name']} {customer['last_name']}",
                "city": customer["city"],
                "scoring_group": customer["scoring_group"],
                "style_cluster": customer["style_cluster"],
                "order_count": customer["order_count"],
                "total_spent": customer["total_spent"],
                "last_order_at": customer["last_order_at"],
                "readiness": readiness_by_key[key],
            }
        )

    return {
        "seed_version": SEED_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "shopify_store_domain": settings.normalized_store_domain,
        "dry_run": settings.dry_run,
        "delete_seeded_data": settings.delete_seeded_data,
        "total_products_created": operation_stats["products_created"],
        "total_customers_created": operation_stats["customers_created"],
        "total_orders_created": operation_stats["orders_created"],
        "products_would_create": len(products) if settings.dry_run else 0,
        "customers_would_create": len(customers) if settings.dry_run else 0,
        "orders_would_create": len(orders) if settings.dry_run else 0,
        "products_planned": len(products),
        "customers_planned": len(customers),
        "orders_planned": len(orders),
        "customers_with_email": sum(1 for customer in customers if customer["email"]),
        "customers_without_email": sum(1 for customer in customers if not customer["email"]),
        "customers_with_phone": sum(1 for customer in customers if customer["phone"]),
        "customers_without_phone": sum(1 for customer in customers if not customer["phone"]),
        "customers_with_email_consent": sum(
            1 for customer in customers if customer["email_marketing_consent"]
        ),
        "customers_without_email_consent": sum(
            1 for customer in customers if not customer["email_marketing_consent"]
        ),
        "customers_with_whatsapp_sms_readiness": sum(
            1 for item in readiness if item["can_whatsapp"]
        ),
        "recommended_channel_counts": dict(recommended_channel_counts),
        "vip_count": group_counts["E_vip"],
        "inactive_count": inactive_count,
        "dead_count": group_counts["D_dead_suppress"],
        "high_intent_count": group_counts["A_high_intent"],
        "medium_intent_count": group_counts["B_medium_intent"],
        "winback_count": group_counts["C_winback"],
        "bad_data_count": tag_counts["retention_bad_data"],
        "no_order_customer_count": no_orders_count,
        "one_time_buyer_count": one_time_count,
        "repeat_buyer_count": repeat_count,
        "scoring_group_counts": dict(group_counts),
        "average_order_value": average_order_value,
        "total_fake_store_revenue": money(net_revenue),
        "gross_fake_store_revenue": money(gross_revenue),
        "top_products": top_products,
        "top_cities": [
            {"city": city, "customers": count}
            for city, count in city_count.most_common(10)
        ],
        "top_style_clusters": [
            {"style_cluster": cluster, "customers": count}
            for cluster, count in cluster_count.most_common()
        ],
        "sample_customer_profiles": sample_customer_profiles,
        "customer_channel_readiness": readiness,
        "recommendation_map_summary": {
            "products_mapped": len(dataset["recommendation_map"]),
            "average_complementary_count": average_map_count(
                dataset["recommendation_map"], "complementary_products"
            ),
            "average_similar_count": average_map_count(
                dataset["recommendation_map"], "similar_products"
            ),
            "average_upsell_count": average_map_count(
                dataset["recommendation_map"], "upsell_products"
            ),
            "average_seasonal_count": average_map_count(
                dataset["recommendation_map"], "seasonal_products"
            ),
        },
        "errors": operation_stats["errors"],
        "skipped_records": operation_stats["skipped_records"],
        "deleted_records": operation_stats["deleted_records"],
    }


def average_map_count(recommendation_map: Dict[str, Any], key: str) -> float:
    if not recommendation_map:
        return 0.0
    return round(
        sum(len(item[key]) for item in recommendation_map.values())
        / len(recommendation_map),
        2,
    )


def initial_operation_stats() -> Dict[str, Any]:
    return {
        "products_created": 0,
        "customers_created": 0,
        "orders_created": 0,
        "products_skipped": 0,
        "customers_skipped": 0,
        "orders_skipped": 0,
        "errors": [],
        "skipped_records": [],
        "deleted_records": {"products": 0, "customers": 0, "orders": 0},
    }


def build_shopify_product_payload(product: Dict[str, Any]) -> Dict[str, Any]:
    variants = []
    for variant in product["variants"]:
        variants.append(
            clean_dict(
                {
                    "option1": variant["size"],
                    "option2": variant["color"],
                    "price": variant["price"],
                    "compare_at_price": variant.get("compare_at_price"),
                    "sku": variant["sku"],
                    "inventory_management": "shopify",
                    "inventory_quantity": variant["inventory_quantity"],
                    "inventory_policy": variant["inventory_policy"],
                    "taxable": True,
                    "requires_shipping": True,
                }
            )
        )

    return {
        "title": product["title"],
        "body_html": product["body_html"],
        "vendor": product["vendor"],
        "product_type": product["product_type"],
        "handle": product["handle"],
        "status": "active",
        "tags": ", ".join(product["tags"]),
        "options": [
            {"name": "Size", "values": product["sizes"]},
            {"name": "Color", "values": product["colors"]},
        ],
        "variants": variants,
        "images": [{"src": url} for url in product["image_urls"]],
    }


def build_shopify_customer_payload(customer: Dict[str, Any]) -> Dict[str, Any]:
    note_parts = [
        f"Retention seed customer key: {customer['customer_key']}",
        f"Scoring group: {customer['scoring_group']}",
        f"Style cluster: {customer['style_cluster']}",
    ]
    if customer.get("raw_phone") and not customer["phone_valid"]:
        note_parts.append(f"Raw malformed phone: {customer['raw_phone']}")
    if customer["address_incomplete"]:
        note_parts.append("Address intentionally incomplete for bad-data tests.")

    payload = {
        "first_name": customer["first_name"],
        "last_name": customer["last_name"],
        "tags": ", ".join(customer["tags"]),
        "note": " | ".join(note_parts),
        "verified_email": True,
        "send_email_welcome": False,
        "accepts_marketing": bool(customer["email_marketing_consent"]),
        "addresses": [customer["address"]] if customer.get("address") else [],
    }
    if customer.get("email"):
        payload["email"] = customer["email"]
        payload["email_marketing_consent"] = {
            "state": "subscribed"
            if customer["email_marketing_consent"]
            else "not_subscribed",
            "opt_in_level": "single_opt_in",
            "consent_updated_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }
    if customer.get("phone") and customer["phone_valid"]:
        payload["phone"] = customer["phone"]
        payload["sms_marketing_consent"] = {
            "state": "subscribed"
            if customer["sms_marketing_consent"]
            else "not_subscribed",
            "opt_in_level": "single_opt_in",
            "consent_collected_from": "SHOPIFY",
            "consent_updated_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }
    return payload


def build_shopify_order_payload(
    order: Dict[str, Any],
    customer: Dict[str, Any],
    shopify_customer_id: int | str,
    variant_id_by_sku: Dict[str, int | str],
) -> Dict[str, Any]:
    line_items = []
    for item in order["line_items"]:
        variant_id = variant_id_by_sku.get(item["variant_sku"])
        if variant_id:
            line_items.append(
                {
                    "variant_id": variant_id,
                    "quantity": item["quantity"],
                }
            )
        else:
            line_items.append(
                {
                    "title": item["product_title"],
                    "price": item["price"],
                    "quantity": item["quantity"],
                    "sku": item["variant_sku"],
                    "requires_shipping": True,
                }
            )

    payload = {
        "customer": {"id": shopify_customer_id},
        "line_items": line_items,
        "processed_at": order["processed_at"],
        "financial_status": order["financial_status"],
        "fulfillment_status": order["fulfillment_status"],
        "tags": ", ".join(order["tags"]),
        "note": (
            f"Retention seed order key: {order['order_key']} | "
            f"Generated for {customer['scoring_group']} / {customer['style_cluster']}"
        ),
        "note_attributes": [
            {"name": "seed_order_key", "value": order["order_key"]},
            {"name": "customer_seed_key", "value": order["customer_key"]},
            {"name": "style_cluster", "value": customer["style_cluster"]},
        ],
        "send_receipt": False,
        "send_fulfillment_receipt": False,
        "inventory_behaviour": "bypass",
    }
    if customer.get("email"):
        payload["email"] = customer["email"]
    if customer.get("phone") and customer["phone_valid"]:
        payload["phone"] = customer["phone"]
    if order.get("shipping_address"):
        payload["shipping_address"] = order["shipping_address"]
    if order.get("billing_address"):
        payload["billing_address"] = order["billing_address"]
    if order.get("discount_code"):
        payload["discount_codes"] = [
            {
                "code": order["discount_code"],
                "amount": order["discount_rate"],
                "type": "percentage",
            }
        ]
    return clean_dict(payload)


def load_state(data_dir: Path) -> Dict[str, Any]:
    return read_json(
        data_dir / STATE_FILE_NAME,
        {"products": {}, "customers": {}, "orders": {}, "last_run_at": None},
    )


def save_state(data_dir: Path, state: Dict[str, Any]) -> None:
    state["last_run_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    write_json(data_dir / STATE_FILE_NAME, state)


def index_remote_seeded(
    resources: Iterable[Dict[str, Any]], tag_prefix: str
) -> Dict[str, Dict[str, Any]]:
    indexed = {}
    for resource in resources:
        key = find_seed_tag_value(resource.get("tags"), tag_prefix)
        if key:
            indexed[key] = resource
    return indexed


def variant_map_from_product(product: Dict[str, Any]) -> Dict[str, int | str]:
    mapping = {}
    for variant in product.get("variants", []):
        sku = variant.get("sku")
        variant_id = variant.get("id")
        if sku and variant_id:
            mapping[sku] = variant_id
    return mapping


def seed_to_shopify(
    settings: AppSettings,
    dataset: Dict[str, Any],
    operation_stats: Dict[str, Any],
) -> None:
    settings.validate_for_live_api()
    data_dir = settings.data_dir
    state = load_state(data_dir)
    state["shopify_store_domain"] = settings.normalized_store_domain
    state.setdefault("products", {})
    state.setdefault("customers", {})
    state.setdefault("orders", {})

    client = ShopifyClient(
        store_domain=settings.normalized_store_domain,
        admin_access_token=settings.shopify_admin_access_token or "",
        api_version=settings.shopify_api_version,
        timeout_seconds=settings.request_timeout_seconds,
        max_retries=settings.max_retries,
        retry_base_delay_seconds=settings.retry_base_delay_seconds,
    )

    if settings.delete_seeded_data:
        delete_seeded_records(client, settings, state, operation_stats)

    print(
        "Fetching existing seeded products, customers, and orders from Shopify...",
        flush=True,
    )
    remote_products = index_remote_seeded(
        client.graphql_seeded_products(settings.seed_tag), "seed_product_"
    )
    remote_customers = index_remote_seeded(
        client.graphql_seeded_customers(settings.seed_tag), "seed_customer_"
    )
    remote_orders = index_remote_seeded(
        client.graphql_seeded_orders(settings.seed_tag), "seed_order_"
    )
    print(
        "Remote seeded records found: "
        f"{len(remote_products)} products, "
        f"{len(remote_customers)} customers, "
        f"{len(remote_orders)} orders.",
        flush=True,
    )

    product_index = {
        key: value for key, value in state.get("products", {}).items() if value.get("id")
    }
    for handle, resource in remote_products.items():
        product_index[handle] = {
            "id": resource["id"],
            "variant_ids_by_sku": variant_map_from_product(resource),
        }
        state["products"][handle] = product_index[handle]

    customer_index = {
        key: value for key, value in state.get("customers", {}).items() if value.get("id")
    }
    for customer_key, resource in remote_customers.items():
        customer_index[customer_key] = {"id": resource["id"]}
        state["customers"][customer_key] = customer_index[customer_key]

    order_index = {
        key: value for key, value in state.get("orders", {}).items() if value.get("id")
    }
    for order_key, resource in remote_orders.items():
        order_index[order_key] = {"id": resource["id"]}
        state["orders"][order_key] = order_index[order_key]

    for index, product in enumerate(dataset["products"], start=1):
        handle = product["handle"]
        if handle in product_index:
            operation_stats["products_skipped"] += 1
            operation_stats["skipped_records"].append(
                {"type": "product", "key": handle, "reason": "already_seeded"}
            )
            continue
        payload = build_shopify_product_payload(product)
        try:
            created = client.create_product(payload)["product"]
        except ShopifyAPIError as exc:
            if exc.status_code == 422 and payload.get("images"):
                retry_payload = deepcopy(payload)
                retry_payload.pop("images", None)
                try:
                    created = client.create_product(retry_payload)["product"]
                    operation_stats["errors"].append(
                        {
                            "type": "product_image_retry",
                            "key": handle,
                            "message": "Created product without remote images after Shopify rejected image URLs.",
                        }
                    )
                except ShopifyAPIError as retry_exc:
                    log_error(operation_stats, "product", handle, retry_exc)
                    continue
            else:
                log_error(operation_stats, "product", handle, exc)
                continue
        product_index[handle] = {
            "id": created["id"],
            "variant_ids_by_sku": variant_map_from_product(created),
        }
        state["products"][handle] = product_index[handle]
        operation_stats["products_created"] += 1
        if index % 25 == 0:
            save_state(data_dir, state)
            print(
                f"Products processed: {index}/{len(dataset['products'])}",
                flush=True,
            )

    for index, customer in enumerate(dataset["customers"], start=1):
        customer_key = customer["customer_key"]
        if customer_key in customer_index:
            operation_stats["customers_skipped"] += 1
            operation_stats["skipped_records"].append(
                {"type": "customer", "key": customer_key, "reason": "already_seeded"}
            )
            continue
        payload = build_shopify_customer_payload(customer)
        try:
            created = client.create_customer(payload)["customer"]
        except ShopifyAPIError as exc:
            log_error(operation_stats, "customer", customer_key, exc)
            continue
        customer_index[customer_key] = {"id": created["id"]}
        state["customers"][customer_key] = customer_index[customer_key]
        operation_stats["customers_created"] += 1
        save_state(data_dir, state)
        if index % 100 == 0:
            print(
                f"Customers processed: {index}/{len(dataset['customers'])}",
                flush=True,
            )

    variant_id_by_sku: Dict[str, int | str] = {}
    for product_state in product_index.values():
        variant_id_by_sku.update(product_state.get("variant_ids_by_sku", {}))

    customers_by_key = {
        customer["customer_key"]: customer for customer in dataset["customers"]
    }
    for index, order in enumerate(dataset["orders"], start=1):
        order_key = order["order_key"]
        if order_key in order_index:
            operation_stats["orders_skipped"] += 1
            operation_stats["skipped_records"].append(
                {"type": "order", "key": order_key, "reason": "already_seeded"}
            )
            continue
        customer_state = customer_index.get(order["customer_key"])
        if not customer_state:
            operation_stats["orders_skipped"] += 1
            operation_stats["skipped_records"].append(
                {
                    "type": "order",
                    "key": order_key,
                    "reason": "customer_not_available",
                    "customer_key": order["customer_key"],
                }
            )
            continue
        customer = customers_by_key[order["customer_key"]]
        payload = build_shopify_order_payload(
            order,
            customer,
            customer_state["id"],
            variant_id_by_sku,
        )
        try:
            created = create_order_with_fallback(client, payload)
        except ShopifyAPIError as exc:
            log_error(operation_stats, "order", order_key, exc)
            continue
        order_index[order_key] = {"id": created["id"]}
        state["orders"][order_key] = order_index[order_key]
        operation_stats["orders_created"] += 1
        save_state(data_dir, state)
        if index % 100 == 0:
            print(f"Orders processed: {index}/{len(dataset['orders'])}", flush=True)

    save_state(data_dir, state)


def create_order_with_fallback(
    client: ShopifyClient, payload: Dict[str, Any]
) -> Dict[str, Any]:
    try:
        return client.create_order(payload)["order"]
    except ShopifyAPIError as exc:
        if exc.status_code != 422:
            raise

    retry_payload = deepcopy(payload)
    retry_payload["financial_status"] = "paid"
    retry_payload.pop("fulfillment_status", None)
    try:
        return client.create_order(retry_payload)["order"]
    except ShopifyAPIError as exc:
        if exc.status_code != 422:
            raise

    retry_payload.pop("discount_codes", None)
    return client.create_order(retry_payload)["order"]


def delete_seeded_records(
    client: ShopifyClient,
    settings: AppSettings,
    state: Dict[str, Any],
    operation_stats: Dict[str, Any],
) -> None:
    print("Deleting existing seeded orders, customers, and products...", flush=True)
    order_ids = {
        str(item.get("id"))
        for item in state.get("orders", {}).values()
        if item.get("id")
    }
    try:
        for order in client.list_seeded_orders(settings.seed_tag):
            order_ids.add(str(order["id"]))
    except ShopifyAPIError as exc:
        log_error(operation_stats, "delete_orders_list", "visible_orders", exc)

    for order_id in sorted(order_ids):
        try:
            client.delete_order(order_id)
            operation_stats["deleted_records"]["orders"] += 1
        except ShopifyAPIError as exc:
            log_error(operation_stats, "delete_order", order_id, exc)

    customer_ids = {
        str(item.get("id"))
        for item in state.get("customers", {}).values()
        if item.get("id")
    }
    try:
        for customer in client.list_seeded_customers(settings.seed_tag):
            customer_ids.add(str(customer["id"]))
    except ShopifyAPIError as exc:
        log_error(operation_stats, "delete_customers_list", "customers", exc)

    for customer_id in sorted(customer_ids):
        try:
            client.delete_customer(customer_id)
            operation_stats["deleted_records"]["customers"] += 1
        except ShopifyAPIError as exc:
            log_error(operation_stats, "delete_customer", customer_id, exc)

    product_ids = {
        str(item.get("id"))
        for item in state.get("products", {}).values()
        if item.get("id")
    }
    try:
        for product in client.list_seeded_products(settings.seed_tag):
            product_ids.add(str(product["id"]))
    except ShopifyAPIError as exc:
        log_error(operation_stats, "delete_products_list", "products", exc)

    for product_id in sorted(product_ids):
        try:
            client.delete_product(product_id)
            operation_stats["deleted_records"]["products"] += 1
        except ShopifyAPIError as exc:
            log_error(operation_stats, "delete_product", product_id, exc)

    state["products"] = {}
    state["customers"] = {}
    state["orders"] = {}


def log_error(
    operation_stats: Dict[str, Any],
    record_type: str,
    key: str,
    exc: ShopifyAPIError,
) -> None:
    operation_stats["errors"].append(
        {
            "type": record_type,
            "key": key,
            "status_code": exc.status_code,
            "message": str(exc),
            "response_body": exc.response_body,
        }
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and optionally seed a realistic Shopify D2C clothing dataset."
    )
    parser.add_argument("--seed", type=int, default=20260425)
    parser.add_argument("--env-file", type=Path, default=ROOT_DIR / ".env")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Override DRY_RUN and call the Shopify Admin API.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Override DRY_RUN and only generate local JSON files.",
    )
    parser.add_argument(
        "--delete-seeded-data",
        action="store_true",
        help="Delete existing seeded records before seeding.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings(args.env_file)
    if args.data_dir:
        settings.data_dir = args.data_dir
    if args.live:
        settings.dry_run = False
    if args.dry_run:
        settings.dry_run = True
    if args.delete_seeded_data:
        settings.delete_seeded_data = True

    generator = RetentionSeedGenerator(seed=args.seed)
    dataset = generator.build()
    operation_stats = initial_operation_stats()

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    write_json(settings.data_dir / "product_seed.json", dataset["products"])
    write_json(settings.data_dir / "customer_seed.json", dataset["customers"])
    write_json(settings.data_dir / "order_seed.json", dataset["orders"])
    write_json(
        settings.data_dir / "recommendation_map.json",
        dataset["recommendation_map"],
    )

    print(
        "Generated local seed files: "
        f"{len(dataset['products'])} products, "
        f"{len(dataset['customers'])} customers, "
        f"{len(dataset['orders'])} orders.",
        flush=True,
    )

    if settings.dry_run:
        print("DRY_RUN=true, no Shopify Admin API calls will be made.", flush=True)
    else:
        seed_to_shopify(settings, dataset, operation_stats)

    report = build_seed_report(dataset, settings, operation_stats)
    write_json(settings.data_dir / "seed_report.json", report)
    print(
        f"Seed report written to {settings.data_dir / 'seed_report.json'}",
        flush=True,
    )


if __name__ == "__main__":
    main()
