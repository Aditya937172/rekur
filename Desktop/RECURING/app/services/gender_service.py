"""
Gender inference utility for customers based on purchase history.
Infers customer gender from product gender tags in their orders.
"""

from collections import Counter
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Customer, Order, OrderItem


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
            if item.product and item.product.tags:
                tags = item.product.tags.lower()
                if "gender_women" in tags:
                    gender_tags.append("women")
                elif "gender_men" in tags:
                    gender_tags.append("men")
                elif "gender_unisex" in tags:
                    gender_tags.append("unisex")

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

    # Infer and store
    return update_customer_gender(db, customer.id)
