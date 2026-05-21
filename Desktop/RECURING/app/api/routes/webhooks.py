from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from sqlalchemy import delete, select, update
from sqlalchemy.orm import selectinload

from app.core.config import load_settings
from app.core.observability import capture_exception, log_pipeline_error, log_pipeline_event
from app.db.session import SessionLocal
from app.models import (
    BuyerMemory,
    Customer,
    CustomerProfile,
    CustomerReply,
    EmailEngagement,
    Event,
    GeneratedMessage,
    GeneratedOutfitImage,
    Order,
    OrderItem,
    Product,
    RetentionCampaignState,
    RetentionSendLog,
    ReturnRefund,
    Store,
    TrackingSession,
)
from app.services.buyer_memory_service import update_buyer_memory_for_customer
from app.services.sync_service import parse_shopify_datetime, update_customer_order_totals
from app.tasks.outfit_tasks import (
    generate_and_send_outfit_task,
    run_generate_and_send_outfit,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_decimal(value: Any, default: str = "0") -> Decimal:
    if value in (None, ""):
        value = default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def as_int(value: Any, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(parsed, 1)


def verify_shopify_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    expected_b64 = base64.b64encode(expected).decode("utf-8")
    return hmac.compare_digest(expected_b64, signature)


async def verified_shopify_payload(
    request: Request,
    x_shopify_hmac_sha256: str,
) -> dict[str, Any]:
    settings = load_settings()
    body = await request.body()

    if settings.shopify_webhook_secret:
        if not x_shopify_hmac_sha256:
            raise HTTPException(status_code=401, detail="Missing webhook signature")
        if not verify_shopify_signature(
            body, x_shopify_hmac_sha256, settings.shopify_webhook_secret
        ):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Shopify webhook body must be an object")
    return payload


def ensure_store_exists(db, store_id: int) -> Store:
    store = db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail=f"Store {store_id} was not found")
    return store


def upsert_product_from_payload(db, store_id: int, payload: dict[str, Any]) -> Product:
    ensure_store_exists(db, store_id)
    shopify_product_id = str(payload.get("id") or "")
    if not shopify_product_id:
        raise HTTPException(status_code=400, detail="Missing Shopify product id")

    product = db.scalar(
        select(Product).where(
            Product.store_id == store_id,
            Product.shopify_product_id == shopify_product_id,
        )
    )

    if not product:
        product = Product(
            store_id=store_id,
            shopify_product_id=shopify_product_id,
            created_at=parse_shopify_datetime(payload.get("created_at")) or utc_now(),
        )
        db.add(product)

    product.title = payload.get("title") or product.title or f"Shopify Product {shopify_product_id}"
    product.handle = payload.get("handle") or product.handle
    product.description = payload.get("body_html") or product.description
    product.tags = payload.get("tags") if payload.get("tags") is not None else product.tags

    variants = payload.get("variants") or []
    if variants and isinstance(variants[0], dict) and variants[0].get("price") is not None:
        product.price = as_decimal(variants[0].get("price"))

    image = payload.get("image") or {}
    images = payload.get("images") or []
    if isinstance(image, dict) and (image.get("src") or image.get("url")):
        product.image_url = image.get("src") or image.get("url")
    elif images and isinstance(images[0], dict):
        product.image_url = images[0].get("src") or images[0].get("url")

    product.updated_at = parse_shopify_datetime(payload.get("updated_at")) or utc_now()
    db.flush()
    logger.info("Upserted product %s for store %s", shopify_product_id, store_id)
    return product


def upsert_customer_from_payload(db, store_id: int, payload: dict[str, Any]) -> Customer:
    ensure_store_exists(db, store_id)
    shopify_customer_id = str(payload.get("id") or "")
    if not shopify_customer_id:
        raise HTTPException(status_code=400, detail="Missing Shopify customer id")

    customer = db.scalar(
        select(Customer).where(
            Customer.store_id == store_id,
            Customer.shopify_customer_id == shopify_customer_id,
        )
    )

    if not customer:
        customer = Customer(
            store_id=store_id,
            shopify_customer_id=shopify_customer_id,
            created_at=parse_shopify_datetime(payload.get("created_at")) or utc_now(),
        )
        db.add(customer)

    city, country = customer_city_country(payload, {})
    customer.email = payload.get("email")
    customer.first_name = payload.get("first_name")
    customer.last_name = payload.get("last_name")
    customer.phone = payload.get("phone")
    customer.city = city
    customer.country = country
    customer.total_orders = int(payload.get("orders_count") or customer.total_orders or 0)
    customer.total_spent = as_decimal(payload.get("total_spent"), str(customer.total_spent or 0))
    db.flush()
    logger.info("Upserted customer %s for store %s", shopify_customer_id, store_id)
    return customer


def delete_product_from_payload(db, store_id: int, payload: dict[str, Any]) -> str:
    ensure_store_exists(db, store_id)
    shopify_product_id = str(payload.get("id") or "")
    if not shopify_product_id:
        raise HTTPException(status_code=400, detail="Missing Shopify product id")

    product = db.scalar(
        select(Product).where(
            Product.store_id == store_id,
            Product.shopify_product_id == shopify_product_id,
        )
    )
    if not product:
        return shopify_product_id

    affected_customer_ids = [
        int(customer_id)
        for customer_id in db.scalars(
            select(Order.customer_id)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .where(
                Order.store_id == store_id,
                Order.customer_id.is_not(None),
                OrderItem.product_id == product.id,
            )
            .distinct()
        ).all()
    ]
    db.execute(
        update(OrderItem)
        .where(OrderItem.product_id == product.id)
        .values(product_id=None)
    )
    db.execute(
        update(Event).where(Event.product_id == product.id).values(product_id=None)
    )
    db.execute(
        update(GeneratedMessage)
        .where(GeneratedMessage.product_id == product.id)
        .values(product_id=None)
    )
    db.delete(product)
    db.flush()
    for customer_id in affected_customer_ids:
        update_buyer_memory_for_customer(db, store_id, customer_id)
    logger.info("Deleted product %s from store %s", shopify_product_id, store_id)
    return shopify_product_id


def delete_customer_from_payload(db, store_id: int, payload: dict[str, Any]) -> str:
    ensure_store_exists(db, store_id)
    shopify_customer_id = str(payload.get("id") or "")
    if not shopify_customer_id:
        raise HTTPException(status_code=400, detail="Missing Shopify customer id")

    customer = db.scalar(
        select(Customer).where(
            Customer.store_id == store_id,
            Customer.shopify_customer_id == shopify_customer_id,
        )
    )
    if not customer:
        return shopify_customer_id

    db.execute(
        update(RetentionSendLog)
        .where(RetentionSendLog.outfit_image_id.in_(
            select(GeneratedOutfitImage.id).where(
                GeneratedOutfitImage.store_id == store_id,
                GeneratedOutfitImage.customer_id == customer.id,
            )
        ))
        .values(outfit_image_id=None)
    )
    db.execute(delete(GeneratedOutfitImage).where(
        GeneratedOutfitImage.store_id == store_id,
        GeneratedOutfitImage.customer_id == customer.id,
    ))
    db.execute(delete(GeneratedMessage).where(
        GeneratedMessage.store_id == store_id,
        GeneratedMessage.customer_id == customer.id,
    ))
    db.execute(
        update(EmailEngagement)
        .where(EmailEngagement.store_id == store_id, EmailEngagement.customer_id == customer.id)
        .values(customer_id=None, send_log_id=None)
    )
    db.execute(delete(RetentionSendLog).where(
        RetentionSendLog.store_id == store_id,
        RetentionSendLog.customer_id == customer.id,
    ))
    db.execute(delete(RetentionCampaignState).where(
        RetentionCampaignState.store_id == store_id,
        RetentionCampaignState.customer_id == customer.id,
    ))
    db.execute(delete(CustomerReply).where(
        CustomerReply.store_id == store_id,
        CustomerReply.customer_id == customer.id,
    ))
    db.execute(delete(BuyerMemory).where(
        BuyerMemory.store_id == store_id,
        BuyerMemory.customer_id == customer.id,
    ))
    db.execute(delete(CustomerProfile).where(
        CustomerProfile.store_id == store_id,
        CustomerProfile.customer_id == customer.id,
    ))
    db.execute(
        update(ReturnRefund)
        .where(ReturnRefund.store_id == store_id, ReturnRefund.customer_id == customer.id)
        .values(customer_id=None)
    )
    db.execute(
        update(TrackingSession)
        .where(TrackingSession.store_id == store_id, TrackingSession.customer_id == customer.id)
        .values(customer_id=None)
    )
    db.execute(
        update(Event)
        .where(Event.store_id == store_id, Event.customer_id == customer.id)
        .values(customer_id=None)
    )
    db.execute(
        update(Order)
        .where(Order.store_id == store_id, Order.customer_id == customer.id)
        .values(customer_id=None)
    )
    db.delete(customer)
    db.flush()
    logger.info("Deleted customer %s from store %s", shopify_customer_id, store_id)
    return shopify_customer_id


def customer_city_country(
    customer_payload: dict[str, Any],
    order_payload: dict[str, Any],
) -> tuple[str | None, str | None]:
    address = customer_payload.get("default_address") or {}
    if not address:
        addresses = customer_payload.get("addresses") or []
        address = addresses[0] if addresses else {}
    if not address:
        address = order_payload.get("shipping_address") or {}
    if not isinstance(address, dict):
        return None, None
    return address.get("city"), address.get("country")


def upsert_customer_for_order(
    db,
    store_id: int,
    payload: dict[str, Any],
) -> Customer | None:
    customer_payload = payload.get("customer") or {}
    if not isinstance(customer_payload, dict):
        customer_payload = {}

    shopify_customer_id = str(customer_payload.get("id") or "")
    if not shopify_customer_id:
        return None

    customer = db.scalar(
        select(Customer).where(
            Customer.store_id == store_id,
            Customer.shopify_customer_id == shopify_customer_id,
        )
    )
    if not customer:
        customer = Customer(
            store_id=store_id,
            shopify_customer_id=shopify_customer_id,
        )
        db.add(customer)
        db.flush()

    shipping_address = payload.get("shipping_address") or {}
    billing_address = payload.get("billing_address") or {}
    city, country = customer_city_country(customer_payload, payload)

    customer.email = (
        customer_payload.get("email")
        or payload.get("email")
        or customer.email
    )
    customer.first_name = customer_payload.get("first_name") or customer.first_name
    customer.last_name = customer_payload.get("last_name") or customer.last_name
    customer.phone = (
        customer_payload.get("phone")
        or payload.get("phone")
        or shipping_address.get("phone")
        or billing_address.get("phone")
        or customer.phone
    )
    customer.city = city or customer.city
    customer.country = country or customer.country
    return customer


def upsert_product_for_line_item(
    db,
    store_id: int,
    line_item: dict[str, Any],
) -> Product | None:
    shopify_product_id = str(line_item.get("product_id") or "")
    if not shopify_product_id:
        return None

    product = db.scalar(
        select(Product).where(
            Product.store_id == store_id,
            Product.shopify_product_id == shopify_product_id,
        )
    )
    if not product:
        product = Product(
            store_id=store_id,
            shopify_product_id=shopify_product_id,
            title=(
                line_item.get("title")
                or line_item.get("name")
                or f"Shopify Product {shopify_product_id}"
            ),
            price=as_decimal(line_item.get("price")),
            tags="",
        )
        db.add(product)
        db.flush()
        return product

    product.title = line_item.get("title") or line_item.get("name") or product.title
    if product.price is None and line_item.get("price") is not None:
        product.price = as_decimal(line_item.get("price"))
    return product


def delivered_at_from_payload(payload: dict[str, Any]) -> datetime:
    fulfillments = payload.get("fulfillments") or []
    if isinstance(fulfillments, list):
        for fulfillment in reversed(fulfillments):
            if not isinstance(fulfillment, dict):
                continue
            delivered_at = parse_shopify_datetime(
                fulfillment.get("delivered_at")
                or fulfillment.get("updated_at")
                or fulfillment.get("created_at")
            )
            if delivered_at:
                return delivered_at
    return (
        parse_shopify_datetime(payload.get("updated_at"))
        or parse_shopify_datetime(payload.get("processed_at"))
        or parse_shopify_datetime(payload.get("created_at"))
        or utc_now()
    )


def upsert_delivered_order_from_payload(
    db,
    store_id: int,
    payload: dict[str, Any],
) -> tuple[Order, Customer, str | None]:
    shopify_order_id = str(payload.get("id") or "")
    if not shopify_order_id:
        raise HTTPException(status_code=400, detail="Missing Shopify order id")

    customer = upsert_customer_for_order(db, store_id, payload)
    if not customer:
        raise HTTPException(status_code=422, detail="Missing Shopify customer id")
    log_pipeline_event(
        "customer_resolved",
        pipeline="shopify_fulfillment_webhook",
        store_id=store_id,
        customer_id=customer.id,
        shopify_customer_id=customer.shopify_customer_id,
    )

    line_items = payload.get("line_items") or []
    if not isinstance(line_items, list) or not line_items:
        raise HTTPException(status_code=422, detail="Order has no line items")

    order = db.scalar(
        select(Order)
        .options(selectinload(Order.items))
        .where(
            Order.store_id == store_id,
            Order.shopify_order_id == shopify_order_id,
        )
    )
    if not order:
        order = Order(store_id=store_id, shopify_order_id=shopify_order_id)
        db.add(order)
        db.flush()

    delivered_at = delivered_at_from_payload(payload)
    order.customer_id = customer.id
    order.currency = payload.get("currency") or order.currency
    order.fulfillment_status = "delivered"
    order.delivered_at = delivered_at
    order.created_at = (
        parse_shopify_datetime(payload.get("created_at"))
        or order.created_at
        or delivered_at
    )

    order.items.clear()
    calculated_total = Decimal("0")
    for line_item in line_items:
        if not isinstance(line_item, dict):
            continue
        product = upsert_product_for_line_item(db, store_id, line_item)
        quantity = as_int(line_item.get("quantity"))
        price = as_decimal(line_item.get("price"))
        calculated_total += price * quantity
        order.items.append(
            OrderItem(
                product_id=product.id if product else None,
                quantity=quantity,
                price=price,
            )
        )

    order.total_price = as_decimal(payload.get("total_price"), str(calculated_total))
    db.flush()
    update_customer_order_totals(db, store_id)
    update_buyer_memory_for_customer(db, store_id, customer.id)
    db.commit()
    db.refresh(order)
    db.refresh(customer)
    log_pipeline_event(
        "order_resolved",
        pipeline="shopify_fulfillment_webhook",
        store_id=store_id,
        customer_id=customer.id,
        order_id=order.id,
        shopify_order_id=order.shopify_order_id,
        fulfillment_status=order.fulfillment_status,
    )

    recipient_email = (
        (payload.get("customer") or {}).get("email")
        or payload.get("email")
        or customer.email
    )
    return order, customer, recipient_email


def refund_amount_from_payload(payload: dict[str, Any]) -> Decimal:
    amount = Decimal("0")
    transactions = payload.get("transactions") or []
    if isinstance(transactions, list):
        for transaction in transactions:
            if not isinstance(transaction, dict):
                continue
            transaction_amount = as_decimal(transaction.get("amount"))
            amount += abs(transaction_amount)

    if amount > 0:
        return amount

    refund_line_items = payload.get("refund_line_items") or []
    if isinstance(refund_line_items, list):
        for refund_line_item in refund_line_items:
            if not isinstance(refund_line_item, dict):
                continue
            subtotal_set = refund_line_item.get("subtotal_set") or {}
            total_tax_set = refund_line_item.get("total_tax_set") or {}
            subtotal = refund_line_item.get("subtotal")
            total_tax = refund_line_item.get("total_tax")
            if isinstance(subtotal_set, dict):
                subtotal = subtotal or (subtotal_set.get("shop_money") or {}).get("amount")
            if isinstance(total_tax_set, dict):
                total_tax = total_tax or (total_tax_set.get("shop_money") or {}).get("amount")
            amount += as_decimal(subtotal) + as_decimal(total_tax)

    if amount > 0:
        return amount

    return abs(as_decimal(payload.get("amount") or payload.get("total_refunded")))


def upsert_refund_from_payload(
    db,
    store_id: int,
    payload: dict[str, Any],
) -> ReturnRefund:
    ensure_store_exists(db, store_id)
    refund_id = str(payload.get("id") or "")
    if not refund_id:
        raise HTTPException(status_code=400, detail="Missing Shopify refund id")

    shopify_order_id = str(payload.get("order_id") or "")
    order = None
    customer_id = None
    if shopify_order_id:
        order = db.scalar(
            select(Order).where(
                Order.store_id == store_id,
                Order.shopify_order_id == shopify_order_id,
            )
        )
        if order:
            customer_id = order.customer_id

    refund = db.scalar(
        select(ReturnRefund).where(
            ReturnRefund.store_id == store_id,
            ReturnRefund.shopify_refund_id == refund_id,
        )
    )
    if not refund:
        refund = ReturnRefund(
            store_id=store_id,
            shopify_refund_id=refund_id,
            created_at=parse_shopify_datetime(payload.get("created_at")) or utc_now(),
        )
        db.add(refund)

    refund.order_id = order.id if order else None
    refund.customer_id = customer_id
    refund.status = "refunded"
    refund.amount = refund_amount_from_payload(payload)
    refund.reason = payload.get("note") or payload.get("reason")
    refund.metadata_json = {
        "shopify_order_id": shopify_order_id or None,
        "processed_at": payload.get("processed_at"),
        "created_at": payload.get("created_at"),
        "refund_line_items": payload.get("refund_line_items") or [],
        "transactions": payload.get("transactions") or [],
    }
    db.flush()
    update_customer_order_totals(db, store_id)
    if customer_id:
        update_buyer_memory_for_customer(db, store_id, customer_id)
    logger.info(
        "Recorded refund %s for store %s order %s amount %s",
        refund_id,
        store_id,
        shopify_order_id or "unknown",
        refund.amount,
    )
    return refund


def queue_outfit_generation(
    *,
    background_tasks: BackgroundTasks,
    store_id: int,
    customer_id: int,
    order_id: int,
    recipient_email: str | None,
) -> tuple[str, str | None]:
    task_kwargs = {
        "store_id": store_id,
        "customer_id": customer_id,
        "order_id": order_id,
        "trigger_reason": "order_delivered_followup",
        "recipient_email": recipient_email,
    }
    try:
        task = generate_and_send_outfit_task.apply_async(kwargs=task_kwargs)
        log_pipeline_event(
            "pipeline_queued",
            pipeline="post_purchase_outfit",
            store_id=store_id,
            customer_id=customer_id,
            order_id=order_id,
            queue_backend="celery",
            task_id=task.id,
        )
        return "celery", task.id
    except Exception as exc:
        logger.warning(
            "Celery queue unavailable; using local background task for order %s: %s",
            order_id,
            exc,
        )
        log_pipeline_event(
            "pipeline_queued",
            pipeline="post_purchase_outfit",
            store_id=store_id,
            customer_id=customer_id,
            order_id=order_id,
            queue_backend="background",
            queue_error=str(exc),
        )
        background_tasks.add_task(run_generate_and_send_outfit, **task_kwargs)
        return "background", None


@router.post("/shopify/{store_id}/orders-fulfilled")
async def shopify_orders_fulfilled(
    store_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    x_shopify_hmac_sha256: str = Header(default=""),
) -> dict:
    payload = await verified_shopify_payload(request, x_shopify_hmac_sha256)
    log_pipeline_event(
        "trigger_received",
        pipeline="shopify_fulfillment_webhook",
        trigger="orders_fulfilled",
        store_id=store_id,
        shopify_order_id=payload.get("id"),
    )

    db = SessionLocal()
    try:
        order, customer, recipient_email = upsert_delivered_order_from_payload(
            db,
            store_id,
            payload,
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Order fulfillment webhook failed: %s", exc, exc_info=True)
        log_pipeline_error(
            "pipeline_failed",
            exc,
            pipeline="shopify_fulfillment_webhook",
            store_id=store_id,
            shopify_order_id=payload.get("id"),
        )
        capture_exception(
            exc,
            pipeline="shopify_fulfillment_webhook",
            store_id=store_id,
            shopify_order_id=payload.get("id"),
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to persist fulfilled Shopify order",
        ) from exc
    finally:
        db.close()

    queue_backend, task_id = queue_outfit_generation(
        background_tasks=background_tasks,
        store_id=store_id,
        customer_id=customer.id,
        order_id=order.id,
        recipient_email=recipient_email,
    )

    logger.info(
        "Queued outfit task for Shopify order %s as local order %s via %s",
        order.shopify_order_id,
        order.id,
        queue_backend,
    )
    return {
        "status": "queued",
        "order_id": order.id,
        "shopify_order_id": order.shopify_order_id,
        "customer_id": customer.id,
        "queue_backend": queue_backend,
        "task_id": task_id,
    }


@router.post("/shopify/{store_id}/refunds-created")
async def shopify_refunds_created(
    store_id: int,
    request: Request,
    x_shopify_hmac_sha256: str = Header(default=""),
) -> dict:
    payload = await verified_shopify_payload(request, x_shopify_hmac_sha256)
    log_pipeline_event(
        "trigger_received",
        pipeline="shopify_refund_webhook",
        trigger="refunds_created",
        store_id=store_id,
        shopify_refund_id=payload.get("id"),
        shopify_order_id=payload.get("order_id"),
    )
    db = SessionLocal()
    try:
        refund = upsert_refund_from_payload(db, store_id, payload)
        db.commit()
        return {
            "status": "recorded",
            "refund_id": refund.shopify_refund_id,
            "local_refund_id": refund.id,
            "order_id": refund.order_id,
            "customer_id": refund.customer_id,
            "amount": str(refund.amount),
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Refund webhook failed: %s", exc, exc_info=True)
        log_pipeline_error("pipeline_failed", exc, pipeline="shopify_refund_webhook", store_id=store_id)
        capture_exception(exc, pipeline="shopify_refund_webhook", store_id=store_id)
        raise HTTPException(status_code=500, detail="Failed to persist Shopify refund") from exc
    finally:
        db.close()


@router.post("/shopify/{store_id}/products-create")
async def shopify_products_create(
    store_id: int,
    request: Request,
    x_shopify_hmac_sha256: str = Header(default=""),
) -> dict:
    payload = await verified_shopify_payload(request, x_shopify_hmac_sha256)
    log_pipeline_event(
        "trigger_received",
        pipeline="shopify_product_webhook",
        trigger="products_create",
        store_id=store_id,
        shopify_product_id=payload.get("id"),
    )
    db = SessionLocal()
    try:
        product = upsert_product_from_payload(db, store_id, payload)
        db.commit()
        return {
            "status": "upserted",
            "product_id": product.id,
            "shopify_product_id": product.shopify_product_id,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Product create webhook failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to persist Shopify product") from exc
    finally:
        db.close()


@router.post("/shopify/{store_id}/products-update")
async def shopify_products_update(
    store_id: int,
    request: Request,
    x_shopify_hmac_sha256: str = Header(default=""),
) -> dict:
    payload = await verified_shopify_payload(request, x_shopify_hmac_sha256)
    log_pipeline_event(
        "trigger_received",
        pipeline="shopify_product_webhook",
        trigger="products_update",
        store_id=store_id,
        shopify_product_id=payload.get("id"),
    )
    db = SessionLocal()
    try:
        product = upsert_product_from_payload(db, store_id, payload)
        db.commit()
        return {
            "status": "upserted",
            "product_id": product.id,
            "shopify_product_id": product.shopify_product_id,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Product update webhook failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to persist Shopify product") from exc
    finally:
        db.close()


@router.post("/shopify/{store_id}/products-delete")
async def shopify_products_delete(
    store_id: int,
    request: Request,
    x_shopify_hmac_sha256: str = Header(default=""),
) -> dict:
    payload = await verified_shopify_payload(request, x_shopify_hmac_sha256)
    log_pipeline_event(
        "trigger_received",
        pipeline="shopify_product_webhook",
        trigger="products_delete",
        store_id=store_id,
        shopify_product_id=payload.get("id"),
    )
    db = SessionLocal()
    try:
        shopify_product_id = delete_product_from_payload(db, store_id, payload)
        db.commit()
        return {"status": "deleted", "product_id": shopify_product_id}
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Product delete webhook failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete Shopify product") from exc
    finally:
        db.close()


@router.post("/shopify/{store_id}/customers-create")
async def shopify_customers_create(
    store_id: int,
    request: Request,
    x_shopify_hmac_sha256: str = Header(default=""),
) -> dict:
    payload = await verified_shopify_payload(request, x_shopify_hmac_sha256)
    log_pipeline_event(
        "trigger_received",
        pipeline="shopify_customer_webhook",
        trigger="customers_create",
        store_id=store_id,
        shopify_customer_id=payload.get("id"),
    )
    db = SessionLocal()
    try:
        customer = upsert_customer_from_payload(db, store_id, payload)
        db.commit()
        return {
            "status": "upserted",
            "customer_id": customer.id,
            "shopify_customer_id": customer.shopify_customer_id,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Customer create webhook failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to persist Shopify customer") from exc
    finally:
        db.close()


@router.post("/shopify/{store_id}/customers-update")
async def shopify_customers_update(
    store_id: int,
    request: Request,
    x_shopify_hmac_sha256: str = Header(default=""),
) -> dict:
    payload = await verified_shopify_payload(request, x_shopify_hmac_sha256)
    log_pipeline_event(
        "trigger_received",
        pipeline="shopify_customer_webhook",
        trigger="customers_update",
        store_id=store_id,
        shopify_customer_id=payload.get("id"),
    )
    db = SessionLocal()
    try:
        customer = upsert_customer_from_payload(db, store_id, payload)
        db.commit()
        return {
            "status": "upserted",
            "customer_id": customer.id,
            "shopify_customer_id": customer.shopify_customer_id,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Customer update webhook failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to persist Shopify customer") from exc
    finally:
        db.close()


@router.post("/shopify/{store_id}/customers-delete")
async def shopify_customers_delete(
    store_id: int,
    request: Request,
    x_shopify_hmac_sha256: str = Header(default=""),
) -> dict:
    payload = await verified_shopify_payload(request, x_shopify_hmac_sha256)
    log_pipeline_event(
        "trigger_received",
        pipeline="shopify_customer_webhook",
        trigger="customers_delete",
        store_id=store_id,
        shopify_customer_id=payload.get("id"),
    )
    db = SessionLocal()
    try:
        shopify_customer_id = delete_customer_from_payload(db, store_id, payload)
        db.commit()
        return {"status": "deleted", "customer_id": shopify_customer_id}
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Customer delete webhook failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete Shopify customer") from exc
    finally:
        db.close()
