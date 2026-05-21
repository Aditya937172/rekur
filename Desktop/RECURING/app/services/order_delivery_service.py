from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.observability import log_pipeline_event
from app.models import Customer, GeneratedOutfitImage, Order, OrderItem, Product, Store
from app.schemas import (
    DeliveredOrderCreateRequest,
    DeliveredOrderPipelineResponse,
    GenerateOutfitImageRequest,
    SendOutfitEmailRequest,
)
from app.services.buyer_memory_service import update_buyer_memory_for_customer
from app.services.outfit_service import (
    OutfitServiceError,
    generate_outfit_for_customer,
    send_outfit_email,
)
from app.services.sync_service import update_customer_order_totals


class OrderDeliveryServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def create_delivered_order_and_trigger_pipeline(
    db: Session,
    store_id: int,
    request: DeliveredOrderCreateRequest,
) -> DeliveredOrderPipelineResponse:
    store = db.get(Store, store_id)
    if not store:
        raise OrderDeliveryServiceError(
            f"Store {store_id} was not found.",
            status_code=404,
        )

    customer = db.get(Customer, request.customer_id)
    if not customer or customer.store_id != store_id:
        raise OrderDeliveryServiceError(
            f"Customer {request.customer_id} was not found.",
            status_code=404,
        )
    log_pipeline_event(
        "trigger_received",
        pipeline="manual_delivered_order",
        store_id=store_id,
        customer_id=customer.id,
        shopify_order_id=request.shopify_order_id,
    )
    log_pipeline_event(
        "customer_resolved",
        pipeline="manual_delivered_order",
        store_id=store_id,
        customer_id=customer.id,
        shopify_customer_id=customer.shopify_customer_id,
    )

    shopify_order_id = request.shopify_order_id or local_shopify_order_id()
    existing_order = db.scalar(
        select(Order).where(
            Order.store_id == store_id,
            Order.shopify_order_id == shopify_order_id,
        )
    )
    if existing_order:
        return trigger_pipeline_for_existing_order(
            db,
            store_id=store_id,
            order=existing_order,
            request=request,
        )

    products = load_products_for_items(db, store_id, request)
    delivered_at = request.delivered_at or utc_now()
    order = Order(
        store_id=store_id,
        shopify_order_id=shopify_order_id,
        customer_id=customer.id,
        currency=request.currency,
        fulfillment_status="delivered",
        delivered_at=delivered_at,
        created_at=delivered_at,
    )
    db.add(order)
    db.flush()

    total_price = Decimal("0")
    for item in request.items:
        product = products[item.product_id]
        price = item.price if item.price is not None else product.price or Decimal("0")
        total_price += Decimal(price) * item.quantity
        order.items.append(
            OrderItem(
                product_id=product.id,
                quantity=item.quantity,
                price=price,
            )
        )

    order.total_price = total_price
    update_customer_order_totals(db, store_id)
    update_buyer_memory_for_customer(db, store_id, customer.id)
    db.commit()
    db.refresh(order)
    log_pipeline_event(
        "order_resolved",
        pipeline="manual_delivered_order",
        store_id=store_id,
        customer_id=customer.id,
        order_id=order.id,
        shopify_order_id=order.shopify_order_id,
    )

    return trigger_pipeline_for_existing_order(
        db,
        store_id=store_id,
        order=order,
        request=request,
    )


def trigger_pipeline_for_existing_order(
    db: Session,
    *,
    store_id: int,
    order: Order,
    request: DeliveredOrderCreateRequest,
) -> DeliveredOrderPipelineResponse:
    if order.customer_id != request.customer_id:
        raise OrderDeliveryServiceError(
            "Existing order belongs to a different customer.",
            status_code=409,
        )

    order.fulfillment_status = "delivered"
    order.delivered_at = order.delivered_at or request.delivered_at or utc_now()
    update_customer_order_totals(db, store_id)
    update_buyer_memory_for_customer(db, store_id, request.customer_id)
    db.commit()
    db.refresh(order)
    log_pipeline_event(
        "order_resolved",
        pipeline="manual_delivered_order",
        store_id=store_id,
        customer_id=request.customer_id,
        order_id=order.id,
        shopify_order_id=order.shopify_order_id,
    )

    existing_outfit = db.scalar(
        select(GeneratedOutfitImage)
        .where(
            GeneratedOutfitImage.store_id == store_id,
            GeneratedOutfitImage.order_id == order.id,
            GeneratedOutfitImage.trigger_reason == "order_delivered_followup",
            GeneratedOutfitImage.status.in_(["generated", "sent"]),
        )
        .order_by(GeneratedOutfitImage.created_at.desc())
    )

    try:
        if existing_outfit:
            outfit_response = generate_outfit_for_existing_response(existing_outfit)
        else:
            outfit_response = generate_outfit_for_customer(
                db,
                store_id,
                GenerateOutfitImageRequest(
                    customer_id=request.customer_id,
                    order_id=order.id,
                    trigger_reason="order_delivered_followup",
                    send_email=False,
                ),
            )

        email_response = None
        if request.send_email:
            target_outfit_id = outfit_response.id
            fresh_outfit = db.get(GeneratedOutfitImage, target_outfit_id)
            if fresh_outfit and fresh_outfit.status != "sent":
                email_response = send_outfit_email(
                    db,
                    target_outfit_id,
                    SendOutfitEmailRequest(recipient_email=request.recipient_email),
                )

        db.refresh(order)
        return DeliveredOrderPipelineResponse(
            order_id=order.id,
            shopify_order_id=order.shopify_order_id,
            customer_id=request.customer_id,
            fulfillment_status=order.fulfillment_status or "delivered",
            delivered_at=order.delivered_at or utc_now(),
            outfit=outfit_response,
            email=email_response,
        )
    except OutfitServiceError as exc:
        raise OrderDeliveryServiceError(str(exc), status_code=exc.status_code) from exc


def load_products_for_items(
    db: Session,
    store_id: int,
    request: DeliveredOrderCreateRequest,
) -> dict[int, Product]:
    product_ids = {item.product_id for item in request.items}
    products = db.scalars(
        select(Product).where(Product.store_id == store_id, Product.id.in_(product_ids))
    ).all()
    product_by_id = {product.id: product for product in products}
    missing = sorted(product_ids - set(product_by_id))
    if missing:
        raise OrderDeliveryServiceError(
            f"Products not found for this store: {missing}",
            status_code=404,
        )
    return product_by_id


def generate_outfit_for_existing_response(outfit: GeneratedOutfitImage):
    from app.services.outfit_service import to_outfit_response

    return to_outfit_response(outfit)


def local_shopify_order_id() -> str:
    return f"local-delivered-{int(utc_now().timestamp())}-{uuid4().hex[:8]}"
