"""
Gender and intended-wearer inference utilities.

Important distinction:
- customer gender/profile is a weak personalization hint
- intended wearer/presentation is product/order driven

A male customer can buy womenswear and a female customer can buy menswear.
Image prompts and pairing products should follow the current order's product
gender tags before using customer/name hints.
"""

from collections import Counter
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Customer, Order, OrderItem, Product


MALE_NAME_HINTS = {
    "aarav",
    "aaravv",
    "aditya",
    "arjun",
    "aryan",
    "atharv",
    "ayaan",
    "dev",
    "dhruv",
    "ishaan",
    "kabir",
    "krish",
    "kunal",
    "manav",
    "naman",
    "rahul",
    "raj",
    "rohan",
    "sahil",
    "samarth",
    "ved",
    "vihaan",
    "vikram",
    "vivaan",
}

FEMALE_NAME_HINTS = {
    "aadhya",
    "aadya",
    "ananya",
    "anya",
    "avani",
    "diya",
    "isha",
    "kavya",
    "kiara",
    "meera",
    "naina",
    "prisha",
    "riya",
    "saanvi",
    "sara",
    "shreya",
    "shruti",
    "siya",
    "tanvi",
    "tara",
    "zara",
}


def infer_gender_from_name(customer: Customer) -> Optional[str]:
    first_name = (customer.first_name or "").strip().lower()
    if not first_name:
        return None
    if first_name in MALE_NAME_HINTS:
        return "men"
    if first_name in FEMALE_NAME_HINTS:
        return "women"
    return None


def gender_from_tags(tags: str | None) -> Optional[str]:
    value = (tags or "").lower()
    if "gender_women" in value:
        return "women"
    if "gender_men" in value:
        return "men"
    if "gender_unisex" in value:
        return "unisex"
    return None


def infer_wearer_gender_from_products(products: list[Product]) -> Optional[str]:
    """Infer intended wearer/presentation from the current products only."""
    genders = [gender_from_tags(product.tags) for product in products]
    genders = [gender for gender in genders if gender]
    if not genders:
        return None

    specific = {gender for gender in genders if gender in {"men", "women"}}
    if len(specific) == 1:
        return next(iter(specific))
    if len(specific) > 1:
        return "mixed"
    return "unisex"


def resolve_wearer_gender(
    customer: Customer | None,
    products: list[Product],
) -> Optional[str]:
    """Resolve image/pairing gender safely.

    Priority:
    1. current order/product gender tags
    2. stored customer profile gender
    3. first-name hint only when products are unknown/unisex
    """
    product_gender = infer_wearer_gender_from_products(products)
    if product_gender:
        return product_gender
    if customer and customer.gender:
        return customer.gender
    if customer:
        return infer_gender_from_name(customer) or product_gender
    return product_gender


def infer_wearer_gender_from_context(
    product_context: list[dict],
) -> Optional[str]:
    """Infer intended wearer/presentation from outfit product context."""
    priority_roles = {
        "purchased",
        "first_purchase",
        "owned",
        "recommended_pairing",
        "recommended_similar",
        "seasonal_gap",
    }
    genders: list[str] = []
    for item in product_context:
        if item.get("role") not in priority_roles:
            continue
        tags = item.get("tags") or []
        tag_text = ",".join(str(tag) for tag in tags)
        gender = gender_from_tags(tag_text)
        if gender:
            genders.append(gender)

    if not genders:
        return None
    specific = {gender for gender in genders if gender in {"men", "women"}}
    if len(specific) == 1:
        return next(iter(specific))
    if len(specific) > 1:
        return "mixed"
    return "unisex"


def infer_customer_gender(db: Session, customer_id: int) -> Optional[str]:
    """
    Infer customer gender from their purchase history.

    Logic:
    1. Check all products customer has purchased
    2. Extract gender tags (gender_men, gender_women, gender_unisex)
    3. Return most common gender, excluding unisex if other genders exist

    Returns: 'men', 'women', 'unisex', or None
    """
    orders = db.query(Order).filter(Order.customer_id == customer_id).all()

    if not orders:
        return None

    gender_tags = []
    for order in orders:
        items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
        for item in items:
            if item.product:
                gender = gender_from_tags(item.product.tags)
                if gender:
                    gender_tags.append(gender)

    if not gender_tags:
        return None

    counter = Counter(gender_tags)

    # If we have specific genders (men/women), prefer those over unisex
    specific_genders = {k: v for k, v in counter.items() if k in ["men", "women"]}

    if specific_genders:
        # Return most common specific gender
        return max(specific_genders.items(), key=lambda x: x[1])[0]

    # Otherwise return most common (likely unisex)
    return counter.most_common(1)[0][0]


def update_customer_gender(db: Session, customer_id: int) -> Optional[str]:
    """
    Update customer's gender field based on purchase history.
    Returns the inferred gender.
    """
    customer = db.get(Customer, customer_id)
    if not customer:
        return None

    inferred_gender = infer_customer_gender(db, customer_id)
    customer.gender = inferred_gender
    db.flush()

    return inferred_gender


def get_customer_gender(db: Session, customer: Customer) -> Optional[str]:
    """
    Get customer gender from stored field or infer from purchases.
    """
    if customer.gender:
        return customer.gender

    inferred_gender = infer_customer_gender(db, customer.id)
    if inferred_gender:
        customer.gender = inferred_gender
        db.flush()
        return inferred_gender
    return infer_gender_from_name(customer)
