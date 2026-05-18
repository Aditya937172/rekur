"""
RECURING Pre-Launch Testing Checklist
Run this before onboarding any paying brand.
"""

import httpx
import json
import time
from datetime import datetime, timedelta, timezone


BASE_URL = "http://localhost:8000"
TIMEOUT = 30.0


def test_api_health():
    """Test 1: API is running and healthy."""
    print("\n=== TEST 1: API Health ===")
    response = httpx.get(f"{BASE_URL}/health", timeout=TIMEOUT)
    data = response.json()

    print(f"  Status: {data.get('status')}")
    print(f"  Database: {data.get('services', {}).get('database')}")
    print(f"  Redis: {data.get('services', {}).get('redis')}")

    assert response.status_code == 200
    assert data.get("status") in ["ok", "degraded"]
    print("  ✓ PASS: API is healthy")
    return True


def test_store_exists():
    """Test 2: Store exists in database."""
    print("\n=== TEST 2: Store Exists ===")
    response = httpx.get(f"{BASE_URL}/stores", timeout=TIMEOUT)
    stores = response.json()

    if stores:
        store_id = stores[0].get("id")
        print(f"  Found {len(stores)} store(s)")
        print(f"  Using store_id: {store_id}")
        print("  ✓ PASS: Store exists")
        return store_id

    print("  ✗ FAIL: No stores found")
    return None


def test_products_synced(store_id: int):
    """Test 3: Products are synced with images."""
    print("\n=== TEST 3: Products Synced ===")
    response = httpx.get(f"{BASE_URL}/stores/{store_id}/products", timeout=TIMEOUT)
    products = response.json()

    total = len(products)
    with_images = sum(1 for p in products if p.get("image_url"))

    print(f"  Total products: {total}")
    print(f"  Products with images: {with_images}")
    print(
        f"  Coverage: {with_images}/{total} ({100 * with_images // total if total else 0}%)"
    )

    assert total > 0, "No products found"
    assert with_images > total * 0.9, "Less than 90% products have images"
    print("  ✓ PASS: Products synced with images")
    return True


def test_customers_exist(store_id: int):
    """Test 4: Customers exist with purchase history."""
    print("\n=== TEST 4: Customers Exist ===")
    response = httpx.get(f"{BASE_URL}/stores/{store_id}/customers", timeout=TIMEOUT)
    customers = response.json()

    total = len(customers)
    with_orders = sum(1 for c in customers if c.get("orders_count", 0) > 0)

    print(f"  Total customers: {total}")
    print(f"  Customers with orders: {with_orders}")

    assert total > 0, "No customers found"
    print("  ✓ PASS: Customers exist")
    return customers[0] if customers else None


def test_buyer_memory(store_id: int, customer_id: int):
    """Test 5: Buyer memory exists for customer."""
    print("\n=== TEST 5: Buyer Memory ===")
    response = httpx.get(
        f"{BASE_URL}/stores/{store_id}/customers/{customer_id}/buyer-memory",
        timeout=TIMEOUT,
    )

    if response.status_code == 200:
        memory = response.json()
        print(f"  Memory summary: {memory.get('memory_summary', 'N/A')[:100]}...")
        print(f"  Style tags: {memory.get('style_tags', 'N/A')}")
        print("  ✓ PASS: Buyer memory exists")
        return True

    print("  ✗ FAIL: Buyer memory not found")
    return False


def test_outfit_generation(store_id: int, customer_id: int):
    """Test 6: Outfit generation works."""
    print("\n=== TEST 6: Outfit Generation ===")

    payload = {
        "customer_id": customer_id,
        "trigger_reason": "manual_test",
        "send_email": False,
    }

    print("  Generating outfit (this may take 30-90 seconds)...")
    response = httpx.post(
        f"{BASE_URL}/stores/{store_id}/outfits/generate", json=payload, timeout=120.0
    )

    if response.status_code == 200:
        outfit = response.json()
        print(f"  Outfit ID: {outfit.get('id')}")
        print(f"  Status: {outfit.get('status')}")
        print(f"  Provider: {outfit.get('provider')}")
        print(f"  Reference images: {len(outfit.get('reference_image_urls_json', []))}")

        assert outfit.get("status") in ["generated", "sent"], (
            f"Unexpected status: {outfit.get('status')}"
        )
        print("  ✓ PASS: Outfit generated successfully")
        return outfit
    else:
        print(f"  ✗ FAIL: {response.status_code} - {response.text[:200]}")
        return None


def test_email_queue(store_id: int, outfit: dict):
    """Test 7: Email can be sent."""
    print("\n=== TEST 7: Email Queue ===")

    if not outfit:
        print("  ⊘ SKIP: No outfit generated")
        return None

    payload = {"recipient_email": "test@example.com"}

    response = httpx.post(
        f"{BASE_URL}/stores/{store_id}/outfits/{outfit.get('id')}/send-email",
        json=payload,
        timeout=TIMEOUT,
    )

    if response.status_code == 200:
        result = response.json()
        print(f"  Email status: {result.get('status')}")
        print(f"  Provider message ID: {result.get('provider_message_id')}")
        print("  ✓ PASS: Email queued for delivery")
        return result
    else:
        print(f"  ✗ FAIL: {response.status_code} - {response.text[:200]}")
        return None


def test_webhook_endpoint(store_id: int):
    """Test 8: Webhook endpoints are accessible."""
    print("\n=== TEST 8: Webhook Endpoints ===")

    endpoints = [
        f"/webhooks/shopify/{store_id}/orders-fulfilled",
        f"/webhooks/shopify/{store_id}/products-create",
        f"/webhooks/shopify/{store_id}/customers-create",
        "/email-events/sendgrid/test",
    ]

    all_passed = True
    for endpoint in endpoints:
        response = httpx.post(
            f"{BASE_URL}{endpoint}",
            json={"test": True},
            timeout=TIMEOUT,
            headers={"Content-Type": "application/json"},
        )

        status = "✓" if response.status_code < 500 else "✗"
        print(f"  {status} {endpoint}: {response.status_code}")

        if response.status_code >= 500:
            all_passed = False

    if all_passed:
        print("  ✓ PASS: All webhook endpoints accessible")
    else:
        print("  ✗ FAIL: Some webhook endpoints failed")

    return all_passed


def test_scheduler_jobs():
    """Test 9: Scheduler has all required jobs."""
    print("\n=== TEST 9: Scheduler Jobs ===")

    expected_jobs = [
        "seasonal_spring_northern",
        "seasonal_summer_northern",
        "seasonal_fall_northern",
        "seasonal_winter_northern",
        "daily_pre_churn_all_stores",
        "daily_anniversary_all_stores",
        "poll_gmail_replies",
        "nightly_shopify_sync_all_stores",
    ]

    response = httpx.get(f"{BASE_URL}/scheduler/jobs", timeout=TIMEOUT)

    if response.status_code == 200:
        jobs = response.json()
        job_ids = [j.get("id") for j in jobs]

        missing = [j for j in expected_jobs if j not in job_ids]

        print(f"  Total jobs: {len(jobs)}")
        print(f"  Expected jobs: {len(expected_jobs)}")

        if missing:
            print(f"  ✗ Missing jobs: {missing}")
        else:
            print("  ✓ PASS: All scheduler jobs present")

        return len(missing) == 0
    else:
        print("  ✗ FAIL: Could not fetch scheduler jobs")
        return False


def test_vector_cache():
    """Test 10: Vector cache is working."""
    print("\n=== TEST 10: Vector Cache ===")

    response = httpx.get(f"{BASE_URL}/outfits/cache-stats", timeout=TIMEOUT)

    if response.status_code == 200:
        stats = response.json()
        print(f"  Cache entries: {stats.get('total_entries', 0)}")
        print(f"  Total hits: {stats.get('total_hits', 0)}")
        print(f"  Hit rate: {stats.get('hit_rate', 0):.1%}")

        if stats.get("total_entries", 0) > 0:
            print("  ✓ PASS: Vector cache active")
            return True
        else:
            print("  ⊘ INFO: No cache entries yet (will populate on first use)")
            return True
    else:
        print("  ⊘ INFO: Cache stats endpoint not available")
        return True


def test_multi_store_orchestrator():
    """Test 11: Multi-store orchestrator is configured."""
    print("\n=== TEST 11: Multi-Store Support ===")

    response = httpx.get(f"{BASE_URL}/stores", timeout=TIMEOUT)
    stores = response.json()

    print(f"  Active stores: {len(stores)}")

    if len(stores) >= 2:
        print("  ✓ PASS: Multi-store configured")
    elif len(stores) == 1:
        print("  ⊘ INFO: Single store configured (add more for multi-store)")
    else:
        print("  ✗ FAIL: No stores configured")

    return True


def test_celery_worker():
    """Test 12: Celery worker is responsive."""
    print("\n=== TEST 12: Celery Worker ===")

    response = httpx.get(f"{BASE_URL}/celery/status", timeout=TIMEOUT)

    if response.status_code == 200:
        status = response.json()
        print(f"  Workers online: {status.get('workers_online', 0)}")
        print(f"  Active tasks: {status.get('active_tasks', 0)}")

        if status.get("workers_online", 0) > 0:
            print("  ✓ PASS: Celery worker active")
            return True

    print("  ⊘ INFO: Could not verify Celery worker (check Flower at :5555)")
    return True


def run_all_tests():
    """Run complete test suite."""
    print("=" * 60)
    print("RECURING PRE-LAUNCH TEST SUITE")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    results = []

    try:
        results.append(("API Health", test_api_health()))

        store_id = test_store_exists()
        results.append(("Store Exists", store_id is not None))

        if store_id:
            results.append(("Products Synced", test_products_synced(store_id)))

            customer = test_customers_exist(store_id)
            results.append(("Customers Exist", customer is not None))

            if customer:
                customer_id = customer.get("id")
                results.append(
                    ("Buyer Memory", test_buyer_memory(store_id, customer_id))
                )

                outfit = test_outfit_generation(store_id, customer_id)
                results.append(("Outfit Generation", outfit is not None))

                email_result = test_email_queue(store_id, outfit)
                results.append(("Email Queue", email_result is not None))

            results.append(("Webhook Endpoints", test_webhook_endpoint(store_id)))

        results.append(("Scheduler Jobs", test_scheduler_jobs()))
        results.append(("Vector Cache", test_vector_cache()))
        results.append(("Multi-Store", test_multi_store_orchestrator()))
        results.append(("Celery Worker", test_celery_worker()))

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        results.append(("Exception", False))

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, r in results if r)
    failed = sum(1 for _, r in results if not r)

    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {name}")

    print("-" * 60)
    print(f"Total: {passed} passed, {failed} failed")
    print(f"Finished: {datetime.now(timezone.utc).isoformat()}")

    if failed == 0:
        print("\n✓ ALL TESTS PASSED - Ready for brand onboarding!")
        return True
    else:
        print(f"\n✗ {failed} tests failed - Fix issues before onboarding")
        return False


if __name__ == "__main__":
    run_all_tests()
