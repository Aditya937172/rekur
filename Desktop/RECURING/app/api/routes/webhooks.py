from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from app.core.config import load_settings
from app.db.session import SessionLocal
from app.models import Customer, Product
from app.schemas import DeliveredOrderCreateRequest, DeliveredOrderItemCreate
from app.tasks.outfit_tasks import generate_and_send_outfit_task

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def verify_shopify_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    expected_b64 = base64.b64encode(expected).decode("utf-8")
    return hmac.compare_digest(expected_b64, signature)


def upsert_product_from_webhook(store_id: int, payload: dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        shopify_product_id = str(payload.get("id", ""))
        if not shopify_product_id:
            return

        product = db.scalar(
            "SELECT * FROM product WHERE store_id = :store_id AND shopify_product_id = :pid",
            {"store_id": store_id, "pid": shopify_product_id},
        )

        if not product:
            from app.models import Product as ProductModel

            product = ProductModel(
                store_id=store_id, shopify_product_id=shopify_product_id
            )
            db.add(product)

        product.title = payload.get("title", "") or product.title
        product.handle = payload.get("handle", "") or product.handle
        product.tags = payload.get("tags", "") or product.tags

        variants = payload.get("variants") or []
        if variants:
            price = variants[0].get("price")
            if price:
                product.price = price

        images = payload.get("images") or []
        if images:
            product.image_url = images[0].get("src") or images[0].get("url")

        product.updated_at = utc_now()
        db.commit()
        logger.info(f"Upserted product {shopify_product_id} for store {store_id}")

    except Exception as e:
        logger.error(f"Product upsert failed: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


def upsert_customer_from_webhook(store_id: int, payload: dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        shopify_customer_id = str(payload.get("id", ""))
        if not shopify_customer_id:
            return

        customer = db.scalar(
            "SELECT * FROM customer WHERE store_id = :store_id AND shopify_customer_id = :cid",
            {"store_id": store_id, "cid": shopify_customer_id},
        )

        if not customer:
            from app.models import Customer as CustomerModel

            customer = CustomerModel(
                store_id=store_id, shopify_customer_id=shopify_customer_id
            )
            db.add(customer)

        customer.email = payload.get("email", "") or customer.email
        customer.first_name = payload.get("first_name", "") or customer.first_name
        customer.last_name = payload.get("last_name", "") or customer.last_name
        customer.phone = payload.get("phone", "") or customer.phone

        if payload.get("created_at"):
            customer.shopify_created_at = payload.get("created_at")

        customer.updated_at = utc_now()
        db.commit()
        logger.info(f"Upserted customer {shopify_customer_id} for store {store_id}")

    except Exception as e:
        logger.error(f"Customer upsert failed: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


def get_customer_and_items(
    payload: dict[str, Any],
    store_id: int,
) -> tuple[Customer | None, list[DeliveredOrderItemCreate], str | None]:
    db = SessionLocal()
    try:
        shopify_customer = payload.get("customer") or {}
        shopify_customer_id = str(shopify_customer.get("id", ""))
        line_items = payload.get("line_items") or []
        email = shopify_customer.get("email")

        if not shopify_customer_id:
            return None, [], None

        customer = db.execute(
            "SELECT * FROM customer WHERE store_id = :store_id AND shopify_customer_id = :sid",
            {"store_id": store_id, "sid": shopify_customer_id},
        ).fetchone()

        if not customer:
            return None, [], None

        customer_obj = db.get(Customer, customer.id)

        items: list[DeliveredOrderItemCreate] = []
        for line_item in line_items:
            shopify_product_id = str(line_item.get("product_id", ""))
            product = db.execute(
                "SELECT id FROM product WHERE store_id = :store_id AND shopify_product_id = :pid",
                {"store_id": store_id, "pid": shopify_product_id},
            ).fetchone()
            if product:
                items.append(
                    DeliveredOrderItemCreate(
                        product_id=product.id,
                        quantity=int(line_item.get("quantity", 1)),
                        price=float(line_item.get("price", 0)),
                    )
                )

        return customer_obj, items, email
    finally:
        db.close()


@router.post("/shopify/{store_id}/orders-fulfilled")
async def shopify_orders_fulfilled(
    store_id: int,
    request: Request,
    x_shopify_hmac_sha256: str = Header(default=""),
) -> dict:
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
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    shopify_order_id = str(payload.get("id", ""))
    customer, items, email = get_customer_and_items(payload, store_id)

    if not customer:
        logger.warning(f"Customer not found for order {shopify_order_id}")
        return {"status": "skipped", "reason": "customer_not_found"}

    if not items:
        logger.warning(f"No matching products for order {shopify_order_id}")
        return {"status": "skipped", "reason": "no_products"}

    generate_and_send_outfit_task.delay(
        store_id=store_id,
        customer_id=customer.id,
        order_id=None,
        trigger_reason="order_delivered_followup",
        recipient_email=email or customer.email,
    )

    logger.info(f"Queued outfit task for order {shopify_order_id}")
    return {
        "status": "queued",
        "order_id": shopify_order_id,
        "customer_id": customer.id,
    }


@router.post("/shopify/{store_id}/refunds-created")
async def shopify_refunds_created(
    store_id: int,
    request: Request,
    x_shopify_hmac_sha256: str = Header(default=""),
) -> dict:
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
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    shopify_order_id = str(payload.get("order_id", ""))
    refund_id = str(payload.get("id", ""))

    logger.info(
        f"Refund {refund_id} received for order {shopify_order_id} in store {store_id}"
    )

    return {"status": "accepted", "refund_id": refund_id}


@router.post("/shopify/{store_id}/products-create")
async def shopify_products_create(
    store_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    x_shopify_hmac_sha256: str = Header(default=""),
) -> dict:
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
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    background_tasks.add_task(
        upsert_product_from_webhook, store_id=store_id, payload=payload
    )

    return {"status": "accepted", "product_id": payload.get("id")}


@router.post("/shopify/{store_id}/products-update")
async def shopify_products_update(
    store_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    x_shopify_hmac_sha256: str = Header(default=""),
) -> dict:
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
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    background_tasks.add_task(
        upsert_product_from_webhook, store_id=store_id, payload=payload
    )

    return {"status": "accepted", "product_id": payload.get("id")}


@router.post("/shopify/{store_id}/products-delete")
async def shopify_products_delete(
    store_id: int,
    request: Request,
    x_shopify_hmac_sha256: str = Header(default=""),
) -> dict:
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
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    shopify_product_id = str(payload.get("id", ""))

    db = SessionLocal()
    try:
        db.execute(
            "DELETE FROM product WHERE store_id = :store_id AND shopify_product_id = :pid",
            {"store_id": store_id, "pid": shopify_product_id},
        )
        db.commit()
        logger.info(f"Deleted product {shopify_product_id} from store {store_id}")
    finally:
        db.close()

    return {"status": "deleted", "product_id": shopify_product_id}


@router.post("/shopify/{store_id}/customers-create")
async def shopify_customers_create(
    store_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    x_shopify_hmac_sha256: str = Header(default=""),
) -> dict:
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
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    background_tasks.add_task(
        upsert_customer_from_webhook, store_id=store_id, payload=payload
    )

    return {"status": "accepted", "customer_id": payload.get("id")}


@router.post("/shopify/{store_id}/customers-update")
async def shopify_customers_update(
    store_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    x_shopify_hmac_sha256: str = Header(default=""),
) -> dict:
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
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    background_tasks.add_task(
        upsert_customer_from_webhook, store_id=store_id, payload=payload
    )

    return {"status": "accepted", "customer_id": payload.get("id")}


@router.post("/shopify/{store_id}/customers-delete")
async def shopify_customers_delete(
    store_id: int,
    request: Request,
    x_shopify_hmac_sha256: str = Header(default=""),
) -> dict:
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
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    shopify_customer_id = str(payload.get("id", ""))

    db = SessionLocal()
    try:
        db.execute(
            "DELETE FROM customer WHERE store_id = :store_id AND shopify_customer_id = :cid",
            {"store_id": store_id, "cid": shopify_customer_id},
        )
        db.commit()
        logger.info(f"Deleted customer {shopify_customer_id} from store {store_id}")
    finally:
        db.close()

    return {"status": "deleted", "customer_id": shopify_customer_id}
