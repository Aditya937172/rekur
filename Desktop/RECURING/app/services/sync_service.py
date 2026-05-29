from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.observability import capture_exception, log_pipeline_error, log_pipeline_event
from app.models import Customer, Order, OrderItem, Product, ReturnRefund, Store, SyncRun
from app.services.nango_service import NangoService, NangoServiceError


class SyncServiceError(RuntimeError):
    pass


@dataclass
class SyncSummary:
    status: str
    products_synced: int
    customers_synced: int
    orders_synced: int


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_shopify_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def as_decimal(value: Any, default: str = "0") -> Decimal:
    if value in (None, ""):
        value = default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def as_shopify_id(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def first_product_price(product: dict[str, Any]) -> Decimal | None:
    variants = product.get("variants") or []
    if variants:
        return as_decimal(variants[0].get("price"))
    return None


def product_image_url(product: dict[str, Any]) -> str | None:
    image = product.get("image") or {}
    if isinstance(image, dict) and image.get("src"):
        return str(image["src"])
    images = product.get("images") or []
    if images and isinstance(images[0], dict):
        return images[0].get("src")
    return None


def product_variant_inventory(product: dict[str, Any]) -> list[dict[str, Any]]:
    variants = product.get("variants") or []
    inventory: list[dict[str, Any]] = []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        title = str(variant.get("title") or "")
        option_values = [
            str(variant.get(key) or "")
            for key in ("option1", "option2", "option3")
            if variant.get(key)
        ]
        inventory.append(
            {
                "id": as_shopify_id(variant.get("id")),
                "title": title,
                "sku": variant.get("sku"),
                "option_values": option_values,
                "inventory_quantity": int(variant.get("inventory_quantity") or 0),
                "inventory_policy": variant.get("inventory_policy"),
                "inventory_management": variant.get("inventory_management"),
                "available": variant_is_available(variant),
            }
        )
    return inventory


def variant_is_available(variant: dict[str, Any]) -> bool:
    if not variant.get("inventory_management"):
        return True
    quantity = int(variant.get("inventory_quantity") or 0)
    if quantity > 0:
        return True
    return str(variant.get("inventory_policy") or "").lower() == "continue"


def product_is_in_stock(product: dict[str, Any]) -> bool:
    variants = product.get("variants") or []
    if not variants:
        return True
    return any(variant_is_available(variant) for variant in variants if isinstance(variant, dict))


def customer_city_country(customer: dict[str, Any]) -> tuple[str | None, str | None]:
    address = customer.get("default_address") or {}
    if not address:
        addresses = customer.get("addresses") or []
        address = addresses[0] if addresses else {}
    if not isinstance(address, dict):
        return None, None
    return address.get("city"), address.get("country")


def log_progress(label: str, count: int, interval: int) -> None:
    if count and count % interval == 0:
        log_pipeline_event(
            "sync_progress",
            pipeline="shopify_sync",
            resource=label,
            count=count,
        )


def sync_store(
    db: Session,
    store_id: int,
    *,
    nango_service: NangoService | None = None,
) -> SyncSummary:
    store = db.get(Store, store_id)
    if not store:
        raise SyncServiceError(f"Store {store_id} was not found.")

    nango = nango_service or NangoService.from_settings()
    sync_run = SyncRun(store_id=store.id, status="running", started_at=utc_now())
    db.add(sync_run)
    db.commit()
    db.refresh(sync_run)
    log_pipeline_event(
        "trigger_received",
        pipeline="shopify_sync",
        store_id=store.id,
        shopify_store_domain=store.shopify_store_domain,
        sync_run_id=sync_run.id,
    )

    try:
        products = nango.fetch_products(store.nango_connection_id)
        customers = nango.fetch_customers(store.nango_connection_id)
        orders = nango.fetch_orders(store.nango_connection_id)

        products_synced = upsert_products(db, store, products)
        customers_synced = upsert_customers(db, store, customers)
        orders_synced = upsert_orders(db, store, orders)
        update_customer_order_totals(db, store.id)
        from app.services.buyer_memory_service import rebuild_buyer_memory_for_store

        rebuild_buyer_memory_for_store(db, store.id)

        sync_run.status = "success"
        sync_run.finished_at = utc_now()
        sync_run.products_synced = products_synced
        sync_run.customers_synced = customers_synced
        sync_run.orders_synced = orders_synced
        db.commit()
        log_pipeline_event(
            "pipeline_completed",
            pipeline="shopify_sync",
            store_id=store.id,
            sync_run_id=sync_run.id,
            products_synced=products_synced,
            customers_synced=customers_synced,
            orders_synced=orders_synced,
        )

        return SyncSummary(
            status="success",
            products_synced=products_synced,
            customers_synced=customers_synced,
            orders_synced=orders_synced,
        )
    except (NangoServiceError, Exception) as exc:
        db.rollback()
        failed_run = db.get(SyncRun, sync_run.id)
        if failed_run:
            failed_run.status = "failed"
            failed_run.finished_at = utc_now()
            failed_run.error_message = str(exc)[:4000]
            db.commit()
        log_pipeline_error(
            "pipeline_failed",
            exc,
            pipeline="shopify_sync",
            store_id=store.id,
            sync_run_id=sync_run.id,
        )
        capture_exception(exc, pipeline="shopify_sync", store_id=store.id)
        raise SyncServiceError(f"Store sync failed: {exc}") from exc


def upsert_products(
    db: Session,
    store: Store,
    products: Iterable[dict[str, Any]],
) -> int:
    count = 0
    for product_data in products:
        shopify_product_id = as_shopify_id(product_data.get("id"))
        if not shopify_product_id:
            continue

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
                created_at=parse_shopify_datetime(product_data.get("created_at"))
                or utc_now(),
            )
            db.add(product)

        product.title = product_data.get("title") or ""
        product.handle = product_data.get("handle")
        product.description = product_data.get("body_html")
        product.price = first_product_price(product_data)
        product.image_url = product_image_url(product_data)
        product.tags = product_data.get("tags")
        product.variant_inventory_json = product_variant_inventory(product_data)
        product.in_stock = product_is_in_stock(product_data)
        product.updated_at = (
            parse_shopify_datetime(product_data.get("updated_at")) or utc_now()
        )

        count += 1
        log_progress("products", count, 50)

    db.flush()
    return count


def upsert_customers(
    db: Session,
    store: Store,
    customers: Iterable[dict[str, Any]],
) -> int:
    count = 0
    for customer_data in customers:
        shopify_customer_id = as_shopify_id(customer_data.get("id"))
        if not shopify_customer_id:
            continue

        customer = get_or_create_customer(db, store.id, shopify_customer_id)
        city, country = customer_city_country(customer_data)

        customer.first_name = customer_data.get("first_name")
        customer.last_name = customer_data.get("last_name")
        customer.email = customer_data.get("email")
        customer.phone = customer_data.get("phone")
        customer.city = city
        customer.country = country
        customer.total_orders = int(customer_data.get("orders_count") or 0)
        customer.total_spent = as_decimal(customer_data.get("total_spent"))
        customer.created_at = (
            parse_shopify_datetime(customer_data.get("created_at"))
            or customer.created_at
            or utc_now()
        )

        count += 1
        log_progress("customers", count, 200)

    db.flush()
    return count


def upsert_orders(
    db: Session,
    store: Store,
    orders: Iterable[dict[str, Any]],
) -> int:
    product_map = load_product_map(db, store.id)
    count = 0

    for order_data in orders:
        shopify_order_id = as_shopify_id(order_data.get("id"))
        if not shopify_order_id:
            continue

        customer = resolve_order_customer(db, store.id, order_data.get("customer"))
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

        order.customer_id = customer.id if customer else None
        order.total_price = as_decimal(order_data.get("total_price"))
        order.currency = order_data.get("currency")
        order.created_at = (
            parse_shopify_datetime(order_data.get("created_at")) or utc_now()
        )

        order.items.clear()
        for line_item in order_data.get("line_items") or []:
            shopify_product_id = as_shopify_id(line_item.get("product_id"))
            order.items.append(
                OrderItem(
                    product_id=product_map.get(shopify_product_id),
                    quantity=int(line_item.get("quantity") or 1),
                    price=as_decimal(line_item.get("price")),
                )
            )

        count += 1
        log_progress("orders", count, 500)

    db.flush()
    return count


def get_or_create_customer(
    db: Session,
    store_id: int,
    shopify_customer_id: str,
) -> Customer:
    customer = db.scalar(
        select(Customer).where(
            Customer.store_id == store_id,
            Customer.shopify_customer_id == shopify_customer_id,
        )
    )
    if customer:
        return customer
    customer = Customer(store_id=store_id, shopify_customer_id=shopify_customer_id)
    db.add(customer)
    db.flush()
    return customer


def resolve_order_customer(
    db: Session,
    store_id: int,
    customer_data: Any,
) -> Customer | None:
    if not isinstance(customer_data, dict):
        return None
    shopify_customer_id = as_shopify_id(customer_data.get("id"))
    if not shopify_customer_id:
        return None

    customer = get_or_create_customer(db, store_id, shopify_customer_id)
    customer.first_name = customer_data.get("first_name") or customer.first_name
    customer.last_name = customer_data.get("last_name") or customer.last_name
    customer.email = customer_data.get("email") or customer.email
    customer.phone = customer_data.get("phone") or customer.phone
    return customer


def load_product_map(db: Session, store_id: int) -> dict[str | None, int]:
    rows = db.execute(
        select(Product.shopify_product_id, Product.id).where(Product.store_id == store_id)
    ).all()
    return {shopify_id: product_id for shopify_id, product_id in rows}


def update_customer_order_totals(db: Session, store_id: int) -> None:
    customers = db.scalars(select(Customer).where(Customer.store_id == store_id)).all()
    for customer in customers:
        customer.total_orders = 0
        customer.total_spent = Decimal("0")
        customer.last_order_date = None

    aggregates = db.execute(
        select(
            Order.customer_id,
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_price), 0),
            func.max(Order.created_at),
        )
        .where(Order.store_id == store_id, Order.customer_id.is_not(None))
        .group_by(Order.customer_id)
    ).all()
    refund_customer_id = func.coalesce(ReturnRefund.customer_id, Order.customer_id)
    refund_rows = db.execute(
        select(
            refund_customer_id.label("customer_id"),
            func.coalesce(func.sum(ReturnRefund.amount), 0),
        )
        .outerjoin(Order, ReturnRefund.order_id == Order.id)
        .where(
            ReturnRefund.store_id == store_id,
            refund_customer_id.is_not(None),
        )
        .group_by(refund_customer_id)
    ).all()
    refund_totals = {
        int(customer_id): as_decimal(total_refunded)
        for customer_id, total_refunded in refund_rows
    }

    customer_by_id = {customer.id: customer for customer in customers}
    for customer_id, order_count, total_spent, last_order_date in aggregates:
        customer = customer_by_id.get(customer_id)
        if not customer:
            continue
        customer.total_orders = int(order_count or 0)
        net_spent = as_decimal(total_spent) - refund_totals.get(
            int(customer_id),
            Decimal("0"),
        )
        customer.total_spent = max(net_spent, Decimal("0"))
        customer.last_order_date = last_order_date

    db.flush()
