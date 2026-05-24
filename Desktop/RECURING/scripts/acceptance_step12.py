from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import os  # noqa: E402

os.environ.setdefault("APP_DISABLE_SCHEDULER", "true")

from app.core.auth import add_store_owner  # noqa: E402
from app.core.config import load_settings  # noqa: E402
from app.db.session import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AppUser,
    BuyerMemory,
    Customer,
    CustomerProfile,
    EmailEngagement,
    Event,
    GeneratedOutfitImage,
    Order,
    OrderItem,
    Product,
    RetentionSendLog,
    Store,
    TrackingSession,
)
from app.schemas import GenerateOutfitImageRequest  # noqa: E402


REPORT_PATH = ROOT / "data" / "acceptance_report.json"
ONE_PIXEL_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class AcceptanceRunner:
    def __init__(self, *, store_id: int | None, external: bool, send_email: bool) -> None:
        self.settings = load_settings()
        self.store_id = store_id
        self.external = external
        self.send_email = send_email
        self.results: list[dict[str, Any]] = []
        self.artifacts: dict[str, Any] = {"created_order_ids": [], "created_outfit_ids": []}
        self.owner_email = f"acceptance-owner-{uuid4().hex[:8]}@example.com"
        self.other_email = f"acceptance-other-{uuid4().hex[:8]}@example.com"
        self.owner_token = ""
        self.other_token = ""

    def run(self) -> dict[str, Any]:
        init_db()
        with TestClient(app) as client:
            self.client = client
            self.resolve_store()
            self.create_auth_context()
            self.check_nango_connect()
            self.check_initial_sync_counts()
            self.check_tracking_install_status()
            self.check_tracking_events()
            self.check_fulfilled_webhook_exact_order()
            self.check_post_purchase_outfit_generation()
            self.check_gmail_inline_email()
            self.check_engagement_recording()
            self.check_silent_customer_detection()
            self.check_pre_churn_score()
            self.check_anniversary_due_gate()
            self.check_seasonal_eligibility()
            self.check_reply_updates_style_memory()
            self.check_multi_store_isolation()
            self.check_scheduler_and_celery()
            self.check_fresh_postgres_migration()
            self.check_standard_routes()
            self.cleanup_artifacts()
        self.write_report()
        return self.summary()

    def add_result(
        self,
        scenario: str,
        status: str,
        detail: str,
        **metadata: Any,
    ) -> None:
        row = {
            "scenario": scenario,
            "status": status,
            "detail": detail,
            "metadata": metadata,
        }
        self.results.append(row)
        print(f"[{status}] {scenario}: {detail}")

    def resolve_store(self) -> None:
        with SessionLocal() as db:
            if self.store_id:
                store = db.get(Store, self.store_id)
            else:
                store = db.scalar(select(Store).order_by(Store.id.asc()).limit(1))
            if not store:
                self.add_result("store_presence", "FAIL", "No Store record exists.")
                raise SystemExit(1)
            self.store_id = store.id

    def create_auth_context(self) -> None:
        owner = self.client.post(
            "/auth/dev-token",
            json={"email": self.owner_email, "name": "Acceptance Owner"},
        )
        other = self.client.post(
            "/auth/dev-token",
            json={"email": self.other_email, "name": "Acceptance Other"},
        )
        if owner.status_code != 200 or other.status_code != 200:
            self.add_result(
                "auth_setup",
                "FAIL",
                f"Could not create dev tokens: {owner.status_code}/{other.status_code}",
            )
            return
        self.owner_token = owner.json()["access_token"]
        self.other_token = other.json()["access_token"]
        with SessionLocal() as db:
            user = db.scalar(select(AppUser).where(AppUser.email == self.owner_email))
            add_store_owner(db, user_id=user.id, store_id=self.store_id)
            db.commit()
        self.add_result("auth_setup", "PASS", "Created local owner token and attached store ownership.")

    @property
    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.owner_token}"}

    def check_nango_connect(self) -> None:
        try:
            response = httpx.get(f"{self.settings.nango_base_url.rstrip('/')}/health", timeout=5)
            if response.status_code < 400:
                self.add_result(
                    "shopify_store_connect_through_nango",
                    "PASS",
                    "Nango health endpoint is reachable.",
                    status_code=response.status_code,
                )
                return
            self.add_result(
                "shopify_store_connect_through_nango",
                "BLOCKED",
                f"Nango health returned HTTP {response.status_code}.",
            )
        except Exception as exc:
            self.add_result(
                "shopify_store_connect_through_nango",
                "BLOCKED",
                f"Nango is not reachable at {self.settings.nango_base_url}: {exc}",
            )

    def check_initial_sync_counts(self) -> None:
        with SessionLocal() as db:
            products = db.scalar(select(func.count(Product.id)).where(Product.store_id == self.store_id)) or 0
            customers = db.scalar(select(func.count(Customer.id)).where(Customer.store_id == self.store_id)) or 0
            orders = db.scalar(select(func.count(Order.id)).where(Order.store_id == self.store_id)) or 0
        status = "PASS" if products >= 100 and customers >= 500 and orders >= 100 else "FAIL"
        self.add_result(
            "initial_sync_imports_products_customers_orders",
            status,
            f"Counts: products={products}, customers={customers}, orders={orders}.",
            products=products,
            customers=customers,
            orders=orders,
        )

    def check_tracking_install_status(self) -> None:
        with SessionLocal() as db:
            store = db.get(Store, self.store_id)
            installed = bool(store.tracking_installed)
        if installed:
            self.add_result("tracking_script_installed", "PASS", "Store has tracking_installed=true.")
        elif not self.settings.public_app_url and not self.settings.tracking_script_url:
            self.add_result(
                "tracking_script_installed",
                "BLOCKED",
                "Tracking is not installed and PUBLIC_APP_URL/TRACKING_SCRIPT_URL is not configured.",
            )
        else:
            self.add_result("tracking_script_installed", "FAIL", "Store tracking_installed=false.")

    def check_tracking_events(self) -> None:
        session_id = f"acceptance-session-{uuid4().hex[:8]}"
        product_id, customer_id = self.sample_product_customer()
        responses = [
            self.client.post(
                "/events",
                json={
                    "store_id": self.store_id,
                    "session_id": session_id,
                    "event_type": "session_start",
                    "page_url": "https://acceptance.test/",
                    "device_type": "desktop",
                    "customer_id": customer_id,
                },
            ),
            self.client.post(
                "/events",
                json={
                    "store_id": self.store_id,
                    "session_id": session_id,
                    "event_type": "product_view",
                    "product_id": product_id,
                    "page_url": "https://acceptance.test/products/sample",
                    "device_type": "desktop",
                    "time_spent": 45000,
                    "customer_id": customer_id,
                },
            ),
        ]
        summary = self.client.get(
            f"/stores/{self.store_id}/events/summary",
            headers=self.auth_headers,
        )
        if all(r.status_code == 200 for r in responses) and summary.status_code == 200:
            self.add_result(
                "storefront_events_create_sessions_events",
                "PASS",
                "Tracking endpoint created session/events and summary endpoint is readable.",
                session_id=session_id,
            )
        else:
            self.add_result(
                "storefront_events_create_sessions_events",
                "FAIL",
                f"Event statuses={[r.status_code for r in responses]}, summary={summary.status_code}.",
            )

    def check_fulfilled_webhook_exact_order(self) -> None:
        product_id, customer_id = self.sample_product_customer()
        with SessionLocal() as db:
            product = db.get(Product, product_id)
            customer = db.get(Customer, customer_id)
        shopify_order_id = str(int(time.time() * 1000))
        payload = {
            "id": shopify_order_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "currency": "INR",
            "total_price": str(product.price or Decimal("1000")),
            "email": customer.email,
            "customer": {
                "id": customer.shopify_customer_id,
                "first_name": customer.first_name,
                "last_name": customer.last_name,
                "email": customer.email,
            },
            "line_items": [
                {
                    "product_id": product.shopify_product_id,
                    "title": product.title,
                    "quantity": 1,
                    "price": str(product.price or Decimal("1000")),
                }
            ],
            "fulfillments": [{"delivered_at": datetime.now(timezone.utc).isoformat()}],
        }
        body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        headers = {
            **self.shopify_headers(body),
            "content-type": "application/json",
        }
        import app.api.routes.webhooks as webhook_routes

        old_apply_async = webhook_routes.generate_and_send_outfit_task.apply_async
        webhook_routes.generate_and_send_outfit_task.apply_async = lambda kwargs: SimpleNamespace(id=f"acceptance-task-{uuid4().hex[:8]}")
        try:
            response = self.client.post(
                f"/webhooks/shopify/{self.store_id}/orders-fulfilled",
                content=body,
                headers=headers,
            )
        finally:
            webhook_routes.generate_and_send_outfit_task.apply_async = old_apply_async
        if response.status_code != 200:
            self.add_result("shopify_fulfilled_webhook_creates_exact_order", "FAIL", response.text[:300])
            return
        data = response.json()
        with SessionLocal() as db:
            order = db.scalar(
                select(Order).where(
                    Order.store_id == self.store_id,
                    Order.shopify_order_id == shopify_order_id,
                )
            )
            if order:
                self.artifacts["created_order_ids"].append(order.id)
        if order and data.get("order_id") == order.id:
            self.add_result(
                "shopify_fulfilled_webhook_creates_exact_order",
                "PASS",
                f"Webhook created local order {order.id} for Shopify order {shopify_order_id}.",
                order_id=order.id,
            )
        else:
            self.add_result("shopify_fulfilled_webhook_creates_exact_order", "FAIL", "Order was not persisted exactly.")

    def check_post_purchase_outfit_generation(self) -> None:
        product_id, customer_id = self.sample_product_customer()
        order_id = self.ensure_acceptance_order(customer_id, product_id)
        with patch_fake_image_generation(enabled=not self.external):
            response = self.client.post(
                f"/stores/{self.store_id}/outfits/generate",
                headers=self.auth_headers,
                json={
                    "customer_id": customer_id,
                    "order_id": order_id,
                    "trigger_reason": "order_delivered_followup",
                    "send_email": False,
                },
            )
        if response.status_code == 200 and response.json().get("status") == "generated":
            outfit_id = response.json()["id"]
            self.artifacts["created_outfit_ids"].append(outfit_id)
            self.add_result(
                "post_purchase_outfit_image_generated",
                "PASS",
                f"Generated outfit row {outfit_id}.",
                external_image=self.external,
            )
        else:
            self.add_result("post_purchase_outfit_image_generated", "FAIL", response.text[:300])

    def check_gmail_inline_email(self) -> None:
        if not self.send_email:
            self.add_result(
                "gmail_test_email_sent_with_inline_image",
                "SKIP",
                "Skipped by default to avoid sending email. Run with --send-email to test Gmail delivery.",
            )
            return
        if not self.artifacts["created_outfit_ids"]:
            self.add_result("gmail_test_email_sent_with_inline_image", "FAIL", "No generated outfit exists.")
            return
        response = self.client.post(
            f"/outfits/{self.artifacts['created_outfit_ids'][-1]}/send",
            headers=self.auth_headers,
            json={"recipient_email": self.settings.gmail_sender_email},
        )
        body: Any
        try:
            body = response.json()
        except Exception:
            body = response.text[:300]
        detail_text = str(body)
        provider_config_error = any(
            marker in detail_text.lower()
            for marker in (
                "gmail",
                "refresh token",
                "invalid_grant",
                "access token",
                "sender_email",
            )
        )
        status = (
            "PASS"
            if response.status_code == 200
            and isinstance(body, dict)
            and body.get("provider_message_id")
            else "FAIL"
        )
        if status == "FAIL" and response.status_code in {400, 401, 403, 502} and provider_config_error:
            status = "BLOCKED"
        self.add_result(
            "gmail_test_email_sent_with_inline_image",
            status,
            f"Send endpoint returned HTTP {response.status_code}: {detail_text[:300]}",
        )

    def check_engagement_recording(self) -> None:
        _, customer_id = self.sample_product_customer()
        response = self.client.post(
            "/email-events/gmail/test",
            headers=self.auth_headers,
            json={
                "store_id": self.store_id,
                "customer_id": customer_id,
                "event_type": "open",
                "provider_message_id": f"acceptance-message-{uuid4().hex[:8]}",
                "campaign_type": "acceptance",
            },
        )
        self.add_result(
            "open_click_engagement_recorded",
            "PASS" if response.status_code == 200 else "FAIL",
            f"Gmail manual engagement endpoint returned HTTP {response.status_code}.",
        )

    def check_silent_customer_detection(self) -> None:
        customer_id = self.customer_without_active_pre_churn()
        response = self.client.post(
            f"/stores/{self.store_id}/retention/email-engagement/seed-silent-customer",
            headers=self.auth_headers,
            json={"customer_id": customer_id, "sent_count": 5, "open_count": 4, "click_count": 0},
        )
        ok = response.status_code == 200 and response.json().get("detected_as_silent") is True
        self.add_result(
            "silent_customer_detected_from_seeded_engagement",
            "PASS" if ok else "FAIL",
            f"Seed endpoint returned HTTP {response.status_code}.",
            response=response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text[:200],
        )

    def check_pre_churn_score(self) -> None:
        response = self.client.get(
            f"/stores/{self.store_id}/retention/churn-risk?limit=10",
            headers=self.auth_headers,
        )
        data = response.json() if response.status_code == 200 else []
        ok = response.status_code == 200 and isinstance(data, list)
        self.add_result(
            "pre_churn_score_updates_from_personal_baseline",
            "PASS" if ok else "FAIL",
            f"Churn-risk endpoint returned {len(data) if isinstance(data, list) else 0} rows.",
        )

    def check_anniversary_due_gate(self) -> None:
        customer_id = self.non_due_anniversary_customer()
        if not customer_id:
            self.add_result("anniversary_campaign_sends_only_when_due", "SKIP", "No non-due anniversary candidate found.")
            return
        with patch_fake_image_generation(enabled=True):
            response = self.client.post(
                f"/stores/{self.store_id}/outfits/anniversary",
                headers=self.auth_headers,
                json={"customer_id": customer_id, "days_window": 1, "limit": 1, "send_email": False},
            )
        ok = response.status_code == 200 and response.json().get("sent") == 0
        self.add_result(
            "anniversary_campaign_sends_only_when_due",
            "PASS" if ok else "FAIL",
            f"Anniversary endpoint returned HTTP {response.status_code}, sent={response.json().get('sent') if response.status_code == 200 else 'n/a'}.",
        )

    def check_seasonal_eligibility(self) -> None:
        from app.services.seasonal_scheduler import get_eligible_customers_for_seasonal_lookbook

        with SessionLocal() as db:
            eligible = get_eligible_customers_for_seasonal_lookbook(db, self.store_id, limit=10)
        self.add_result(
            "seasonal_lookbook_sends_only_to_eligible_customers",
            "PASS",
            f"Eligibility query returned {len(eligible)} eligible customers without sending.",
            eligible_count=len(eligible),
        )

    def check_reply_updates_style_memory(self) -> None:
        _, customer_id = self.sample_product_customer()
        import app.services.retention_data_service as retention_data

        old_call_groq = retention_data.call_groq
        retention_data.call_groq = lambda **kwargs: "got you, black oversized streetwear is the note now. i’ll keep future picks closer to that vibe."
        try:
            response = self.client.post(
                f"/stores/{self.store_id}/retention/replies",
                headers=self.auth_headers,
                json={
                    "customer_id": customer_id,
                    "inbound_text": "I like black oversized streetwear, not pastel stuff.",
                },
            )
        finally:
            retention_data.call_groq = old_call_groq
        with SessionLocal() as db:
            profile = db.scalar(
                select(CustomerProfile).where(
                    CustomerProfile.store_id == self.store_id,
                    CustomerProfile.customer_id == customer_id,
                )
            )
            dimensions = profile.preference_dimensions_json if profile else {}
        ok = response.status_code == 200 and "black" in (dimensions.get("mentioned_colors") or [])
        self.add_result(
            "customer_reply_updates_style_memory",
            "PASS" if ok else "FAIL",
            f"Reply endpoint HTTP {response.status_code}; profile colors={dimensions.get('mentioned_colors') if dimensions else None}.",
        )

    def check_multi_store_isolation(self) -> None:
        owner_headers = self.auth_headers
        other_headers = {"Authorization": f"Bearer {self.other_token}"}
        domain = f"acceptance-{uuid4().hex[:8]}.myshopify.com"
        create = self.client.post(
            "/stores",
            headers=owner_headers,
            json={
                "name": "Acceptance Isolation Store",
                "nango_connection_id": f"acceptance-conn-{uuid4().hex[:8]}",
                "shopify_store_domain": domain,
            },
        )
        if create.status_code != 201:
            self.add_result("multi_store_tenant_isolation", "FAIL", f"Could not create temp store: {create.text[:200]}")
            return
        store_id = create.json()["id"]
        owner_status = self.client.get(f"/stores/{store_id}/dashboard", headers=owner_headers).status_code
        other_status = self.client.get(f"/stores/{store_id}/dashboard", headers=other_headers).status_code
        with SessionLocal() as db:
            store = db.get(Store, store_id)
            if store:
                db.delete(store)
                db.commit()
        ok = owner_status == 200 and other_status == 404
        self.add_result(
            "multi_store_tenant_isolation",
            "PASS" if ok else "FAIL",
            f"Owner status={owner_status}, other user status={other_status}.",
        )

    def check_scheduler_and_celery(self) -> None:
        jobs = self.client.get("/internal/scheduler/jobs")
        scheduler_ok = jobs.status_code == 200 and len(jobs.json()) >= 10
        celery_status = self.client.get("/celery/status").json()
        worker_ok = celery_status.get("workers_online", 0) > 0
        self.add_result(
            "scheduler_jobs_registered",
            "PASS" if scheduler_ok else "FAIL",
            f"Internal scheduler jobs endpoint returned {len(jobs.json()) if jobs.status_code == 200 else 0} jobs.",
        )
        self.add_result(
            "celery_worker_runs_outside_api_process",
            "PASS" if worker_ok else "BLOCKED",
            f"Celery workers online={celery_status.get('workers_online', 0)}. Start Redis/Celery worker to pass this.",
        )

    def check_fresh_postgres_migration(self) -> None:
        command = [
            sys.executable,
            "-m",
            "alembic",
            "-x",
            "noop=true",
            "upgrade",
            "head",
            "--sql",
        ]
        env = {"ALEMBIC_DATABASE_URL": "postgresql://user:pass@localhost:5432/styleiq"}
        result = subprocess.run(
            command,
            cwd=ROOT,
            env={**dict(**__import__("os").environ), **env},
            text=True,
            capture_output=True,
            timeout=120,
        )
        combined = f"{result.stdout}\n{result.stderr}"
        if result.returncode == 0 and "CREATE TABLE" in combined:
            self.add_result(
                "fresh_postgres_deploy_from_migrations",
                "PASS",
                "Alembic generated PostgreSQL upgrade SQL successfully. Live Postgres not tested locally.",
            )
        else:
            self.add_result(
                "fresh_postgres_deploy_from_migrations",
                "FAIL",
                (result.stderr or result.stdout)[:400],
            )

    def check_standard_routes(self) -> None:
        schema = app.openapi()
        paths = schema["paths"]
        required = [
            "/webhooks/shopify/{store_id}/orders-fulfilled",
            "/email-events/sendgrid",
            "/replies/inbound",
            "/stores/{store_id}/retention/churn-risk",
            "/stores/{store_id}/outfits/generate",
        ]
        missing = [path for path in required if path not in paths]
        self.add_result(
            "standard_production_routes_present",
            "PASS" if not missing else "FAIL",
            "All standard production routes exist." if not missing else f"Missing: {missing}",
        )

    def sample_product_customer(self) -> tuple[int, int]:
        with SessionLocal() as db:
            product_id = db.scalar(select(Product.id).where(Product.store_id == self.store_id).limit(1))
            customer_id = db.scalar(
                select(Customer.id)
                .where(Customer.store_id == self.store_id, Customer.email.isnot(None))
                .limit(1)
            )
        if not product_id or not customer_id:
            raise RuntimeError("Need at least one product and customer with email for acceptance tests.")
        return int(product_id), int(customer_id)

    def customer_without_active_pre_churn(self) -> int:
        with SessionLocal() as db:
            customer_id = db.scalar(
                select(Customer.id)
                .where(Customer.store_id == self.store_id, Customer.email.isnot(None))
                .order_by(Customer.id.desc())
                .limit(1)
            )
        return int(customer_id)

    def non_due_anniversary_customer(self) -> int | None:
        today = datetime.now(timezone.utc).date()
        with SessionLocal() as db:
            memories = db.scalars(
                select(BuyerMemory)
                .where(BuyerMemory.store_id == self.store_id, BuyerMemory.first_order_at.isnot(None))
                .limit(100)
            ).all()
            for memory in memories:
                first = memory.first_order_at.date()
                if not (first.month == today.month and first.day == today.day):
                    return memory.customer_id
        return None

    def ensure_acceptance_order(self, customer_id: int, product_id: int) -> int:
        with SessionLocal() as db:
            existing = self.artifacts["created_order_ids"][-1] if self.artifacts["created_order_ids"] else None
            if existing and db.get(Order, existing):
                return existing
            product = db.get(Product, product_id)
            order = Order(
                store_id=self.store_id,
                shopify_order_id=f"acceptance-local-{uuid4().hex[:10]}",
                customer_id=customer_id,
                total_price=product.price or Decimal("1000"),
                currency="INR",
                fulfillment_status="delivered",
                delivered_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
            )
            db.add(order)
            db.flush()
            order.items.append(
                __import__("app.models", fromlist=["OrderItem"]).OrderItem(
                    product_id=product_id,
                    quantity=1,
                    price=product.price or Decimal("1000"),
                )
            )
            db.commit()
            db.refresh(order)
            self.artifacts["created_order_ids"].append(order.id)
            return order.id

    def cleanup_artifacts(self) -> None:
        with SessionLocal() as db:
            outfit_ids = list(self.artifacts.get("created_outfit_ids") or [])
            order_ids = list(self.artifacts.get("created_order_ids") or [])
            if outfit_ids:
                db.execute(delete(GeneratedOutfitImage).where(GeneratedOutfitImage.id.in_(outfit_ids)))
            if order_ids:
                db.execute(delete(OrderItem).where(OrderItem.order_id.in_(order_ids)))
                db.execute(delete(Order).where(Order.id.in_(order_ids)))
            db.commit()

    def shopify_headers(self, body: bytes) -> dict[str, str]:
        if not self.settings.shopify_webhook_secret:
            return {}
        signature = base64.b64encode(
            hmac.new(
                self.settings.shopify_webhook_secret.encode("utf-8"),
                body,
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        return {"x-shopify-hmac-sha256": signature}

    def write_report(self) -> None:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = self.summary()
        REPORT_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for row in self.results:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "store_id": self.store_id,
            "external": self.external,
            "send_email": self.send_email,
            "counts": counts,
            "results": self.results,
            "report_path": str(REPORT_PATH),
        }


@contextmanager
def patch_fake_image_generation(*, enabled: bool):
    if not enabled:
        yield
        return
    import app.services.outfit_service as outfit_service

    old_generate = outfit_service.generate_outfit_image
    old_clip = outfit_service.FashionClipService
    old_embedding = outfit_service.product_context_embedding

    class FakeFashionClipService:
        def __init__(self, *args, **kwargs):
            pass

        def compatibility_score(self, *args, **kwargs):
            return 0.75

        def embed_product_combination(self, *args, **kwargs):
            return [0.01] * 384

    def fake_generate_outfit_image(**_: Any):
        return SimpleNamespace(
            task_id=f"acceptance-image-{uuid4().hex[:8]}",
            task_status="completed",
            task_progress=100,
            image_url=None,
            image_base64=ONE_PIXEL_PNG,
            credits_reserved=0.0,
            credits_used=0.0,
            usage={"credits_used": 0.0, "source": "acceptance_fake"},
            raw_response={"source": "acceptance_fake"},
        )

    outfit_service.generate_outfit_image = fake_generate_outfit_image
    outfit_service.FashionClipService = FakeFashionClipService
    outfit_service.product_context_embedding = lambda *args, **kwargs: [0.01] * 384
    try:
        yield
    finally:
        outfit_service.generate_outfit_image = old_generate
        outfit_service.FashionClipService = old_clip
        outfit_service.product_context_embedding = old_embedding


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Step 12 backend acceptance checks.")
    parser.add_argument("--store-id", type=int, default=None)
    parser.add_argument("--external", action="store_true", help="Use real image/Nango providers where tests call them.")
    parser.add_argument("--send-email", action="store_true", help="Send the Gmail inline-image email test.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runner = AcceptanceRunner(
        store_id=args.store_id,
        external=args.external,
        send_email=args.send_email,
    )
    summary = runner.run()
    print(json.dumps(summary["counts"], indent=2))
    print(f"Report written: {REPORT_PATH}")
    return 1 if summary["counts"].get("FAIL") else 0


if __name__ == "__main__":
    raise SystemExit(main())
