from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import AppSettings, load_settings
from app.db.session import SessionLocal
from app.models import Customer, Product, Store

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def shopify_api_request(
    store: Store,
    endpoint: str,
    *,
    settings: AppSettings | None = None,
) -> list[dict]:
    settings = settings or load_settings()

    headers = {
        "X-Shopify-Access-Token": store.shopify_admin_access_token
        or settings.shopify_admin_access_token,
        "Content-Type": "application/json",
    }

    url = f"https://{store.shopify_store_domain}/admin/api/{settings.shopify_api_version}/{endpoint}"

    all_items = []
    params = {"limit": 250}

    while url:
        response = requests.get(url, headers=headers, params=params, timeout=60)
        if response.status_code >= 400:
            logger.error(
                f"Shopify API error: {response.status_code} - {response.text[:500]}"
            )
            break

        data = response.json()

        if "products" in data:
            all_items.extend(data["products"])
            link = response.headers.get("Link", "")
            if 'rel="next"' in link:
                url = link.split(";")[0].strip("<>").strip()
                params = {}
            else:
                break
        elif "customers" in data:
            all_items.extend(data["customers"])
            link = response.headers.get("Link", "")
            if 'rel="next"' in link:
                url = link.split(";")[0].strip("<>").strip()
                params = {}
            else:
                break
        else:
            all_items.extend(data if isinstance(data, list) else [data])
            break

    return all_items


def sync_products_for_store(
    store_id: int, *, settings: AppSettings | None = None
) -> dict:
    settings = settings or load_settings()
    db = SessionLocal()

    stats = {"created": 0, "updated": 0, "deleted": 0, "errors": 0}

    try:
        store = db.get(Store, store_id)
        if not store:
            logger.error(f"Store {store_id} not found")
            return stats

        products = shopify_api_request(store, "products.json", settings=settings)
        existing_ids = set()

        for product_data in products:
            try:
                shopify_product_id = str(product_data.get("id", ""))
                if not shopify_product_id:
                    continue

                existing_ids.add(shopify_product_id)

                product = db.scalar(
                    select(Product).where(
                        Product.store_id == store_id,
                        Product.shopify_product_id == shopify_product_id,
                    )
                )

                if not product:
                    product = Product(
                        store_id=store_id, shopify_product_id=shopify_product_id
                    )
                    db.add(product)
                    stats["created"] += 1

                product.title = product_data.get("title", "")
                product.handle = product_data.get("handle", "")
                product.tags = product_data.get("tags", "")

                variants = product_data.get("variants") or []
                if variants:
                    product.price = variants[0].get("price")

                images = product_data.get("images") or []
                if images:
                    product.image_url = images[0].get("src") or images[0].get("url")

                product.updated_at = utc_now()
                stats["updated"] += 1

            except Exception as e:
                logger.error(f"Product sync error: {e}")
                stats["errors"] += 1

        deleted = db.execute(
            "DELETE FROM product WHERE store_id = :store_id AND shopify_product_id NOT IN :ids",
            {"store_id": store_id, "ids": tuple(existing_ids) or ("__none__",)},
        )
        stats["deleted"] = deleted.rowcount

        db.commit()
        logger.info(f"Product sync complete for store {store_id}: {stats}")

    except Exception as e:
        logger.error(f"Product sync failed for store {store_id}: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()

    return stats


def sync_customers_for_store(
    store_id: int, *, settings: AppSettings | None = None
) -> dict:
    settings = settings or load_settings()
    db = SessionLocal()

    stats = {"created": 0, "updated": 0, "deleted": 0, "errors": 0}

    try:
        store = db.get(Store, store_id)
        if not store:
            logger.error(f"Store {store_id} not found")
            return stats

        customers = shopify_api_request(store, "customers.json", settings=settings)
        existing_ids = set()

        for customer_data in customers:
            try:
                shopify_customer_id = str(customer_data.get("id", ""))
                if not shopify_customer_id:
                    continue

                existing_ids.add(shopify_customer_id)

                customer = db.scalar(
                    select(Customer).where(
                        Customer.store_id == store_id,
                        Customer.shopify_customer_id == shopify_customer_id,
                    )
                )

                if not customer:
                    customer = Customer(
                        store_id=store_id, shopify_customer_id=shopify_customer_id
                    )
                    db.add(customer)
                    stats["created"] += 1

                customer.email = customer_data.get("email", "") or customer.email
                customer.first_name = (
                    customer_data.get("first_name", "") or customer.first_name
                )
                customer.last_name = (
                    customer_data.get("last_name", "") or customer.last_name
                )
                customer.phone = customer_data.get("phone", "") or customer.phone

                if customer_data.get("created_at"):
                    customer.shopify_created_at = customer_data.get("created_at")

                customer.updated_at = utc_now()
                stats["updated"] += 1

            except Exception as e:
                logger.error(f"Customer sync error: {e}")
                stats["errors"] += 1

        db.commit()
        logger.info(f"Customer sync complete for store {store_id}: {stats}")

    except Exception as e:
        logger.error(f"Customer sync failed for store {store_id}: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()

    return stats


def full_sync_for_store(store_id: int, *, settings: AppSettings | None = None) -> dict:
    settings = settings or load_settings()

    product_stats = sync_products_for_store(store_id, settings=settings)
    customer_stats = sync_customers_for_store(store_id, settings=settings)

    return {
        "store_id": store_id,
        "products": product_stats,
        "customers": customer_stats,
    }
