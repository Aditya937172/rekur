from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import load_settings
from app.services.shopify_client import ShopifyAPIError, ShopifyClient
from scripts.seed_shopify_store import (
    build_shopify_order_payload,
    create_order_with_fallback,
    find_seed_tag_value,
    index_remote_seeded,
    read_json,
    save_state,
)


THREAD_LOCAL = threading.local()
RATE_LIMIT_LOCK = threading.Lock()
NEXT_ALLOWED_ORDER_AT = 0.0


def load_seed_data(data_dir: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    orders = read_json(data_dir / "order_seed.json", [])
    customers = read_json(data_dir / "customer_seed.json", [])
    return orders, {customer["customer_key"]: customer for customer in customers}


def build_client(settings: Any) -> ShopifyClient:
    return ShopifyClient(
        store_domain=settings.normalized_store_domain,
        admin_access_token=settings.shopify_admin_access_token or "",
        api_version=settings.shopify_api_version,
        timeout_seconds=settings.request_timeout_seconds,
        max_retries=settings.max_retries,
        retry_base_delay_seconds=settings.retry_base_delay_seconds,
    )


def thread_client(settings: Any) -> ShopifyClient:
    client = getattr(THREAD_LOCAL, "client", None)
    if client is None:
        client = build_client(settings)
        THREAD_LOCAL.client = client
    return client


def remote_indexes(settings: Any) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    client = build_client(settings)
    products = index_remote_seeded(
        client.graphql_seeded_products(settings.seed_tag), "seed_product_"
    )
    customers = index_remote_seeded(
        client.graphql_seeded_customers(settings.seed_tag), "seed_customer_"
    )
    orders = index_remote_seeded(
        client.graphql_seeded_orders(settings.seed_tag), "seed_order_"
    )
    return products, customers, orders


def variant_ids_by_sku(products: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for product in products.values():
        for variant in product.get("variants", []):
            sku = variant.get("sku")
            variant_id = variant.get("id")
            if sku and variant_id:
                mapping[sku] = variant_id
    return mapping


def create_order_task(
    settings: Any,
    order: Dict[str, Any],
    customer: Dict[str, Any],
    customer_id: str,
    variant_map: Dict[str, str],
    seconds_between_orders: float,
) -> Tuple[str, str, str | None]:
    global NEXT_ALLOWED_ORDER_AT
    client = thread_client(settings)
    payload = build_shopify_order_payload(order, customer, customer_id, variant_map)
    for attempt in range(3):
        try:
            with RATE_LIMIT_LOCK:
                now = time.monotonic()
                if NEXT_ALLOWED_ORDER_AT > now:
                    time.sleep(NEXT_ALLOWED_ORDER_AT - now)
                NEXT_ALLOWED_ORDER_AT = time.monotonic() + seconds_between_orders
            created = create_order_with_fallback(client, payload)
            return order["order_key"], str(created["id"]), None
        except ShopifyAPIError as exc:
            if exc.status_code == 429 and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            return order["order_key"], "", f"{exc.status_code}: {exc}"
    return order["order_key"], "", "unknown_error"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resume only missing Shopify seed orders using remote tag indexes."
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument(
        "--seconds-between-orders",
        type=float,
        default=13.0,
        help="Pace order writes. Shopify dev/trial stores are limited to about 5 orders/minute.",
    )
    parser.add_argument("--data-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    settings.validate_for_live_api()
    if args.data_dir:
        settings.data_dir = args.data_dir

    orders, customers_by_key = load_seed_data(settings.data_dir)
    state = read_json(
        settings.data_dir / "shopify_seed_state.json",
        {"products": {}, "customers": {}, "orders": {}},
    )

    products, remote_customers, remote_orders = remote_indexes(settings)
    state.setdefault("products", {})
    state.setdefault("customers", {})
    state.setdefault("orders", {})

    for handle, product in products.items():
        state["products"][handle] = {
            "id": product.get("id"),
            "variant_ids_by_sku": variant_ids_by_sku({handle: product}),
        }
    for customer_key, customer in remote_customers.items():
        state["customers"][customer_key] = {"id": customer.get("id")}
    for order_key, order in remote_orders.items():
        state["orders"][order_key] = {"id": order.get("id")}
    save_state(settings.data_dir, state)

    variant_map = variant_ids_by_sku(products)
    remote_order_keys = set(remote_orders)
    missing_orders = [
        order
        for order in orders
        if order["order_key"] not in remote_order_keys
        and order["customer_key"] in remote_customers
    ]

    print(
        "Remote seeded records found: "
        f"{len(products)} products, {len(remote_customers)} customers, "
        f"{len(remote_orders)} orders.",
        flush=True,
    )
    print(f"Missing orders to create: {len(missing_orders)}", flush=True)

    if not missing_orders:
        return

    lock = threading.Lock()
    errors: List[Dict[str, str]] = []
    completed = 0

    for batch_start in range(0, len(missing_orders), args.batch_size):
        batch = missing_orders[batch_start : batch_start + args.batch_size]
        batch_errors: List[Dict[str, str]] = []
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = []
            for order in batch:
                customer = customers_by_key[order["customer_key"]]
                customer_id = str(remote_customers[order["customer_key"]]["id"])
                futures.append(
                    executor.submit(
                        create_order_task,
                        settings,
                        order,
                        customer,
                        customer_id,
                        variant_map,
                        args.seconds_between_orders,
                    )
                )

            for future in as_completed(futures):
                order_key, order_id, error = future.result()
                with lock:
                    completed += 1
                    if order_id:
                        state["orders"][order_key] = {"id": order_id}
                        save_state(settings.data_dir, state)
                    else:
                        error_item = {"order_key": order_key, "error": error or ""}
                        errors.append(error_item)
                        batch_errors.append(error_item)
                    if completed % 25 == 0:
                        print(
                            f"Created or attempted {completed}/{len(missing_orders)} "
                            "missing orders.",
                            flush=True,
                        )

        # Re-sync after every batch so a timed-out create is still picked up by tag.
        _, _, remote_orders = remote_indexes(settings)
        for order_key, order in remote_orders.items():
            state["orders"][order_key] = {"id": order.get("id")}
        save_state(settings.data_dir, state)
        remote_order_keys = set(remote_orders)
        remaining = [
            order for order in orders if order["order_key"] not in remote_order_keys
        ]
        print(
            f"Batch complete. Remote order keys: {len(remote_order_keys)}. "
            f"Remaining: {len(remaining)}. "
            f"Batch errors: {len(batch_errors)}.",
            flush=True,
        )
        if batch_errors:
            print(f"First batch error: {batch_errors[0]}", flush=True)

    if errors:
        error_path = settings.data_dir / "order_resume_errors.json"
        error_path.write_text(json.dumps(errors, indent=2), encoding="utf-8")
        print(f"Order resume errors written to {error_path}", flush=True)


if __name__ == "__main__":
    main()
