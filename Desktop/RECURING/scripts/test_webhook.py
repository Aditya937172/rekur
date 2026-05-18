"""Test Shopify webhook reception locally."""

import json
import hashlib
import hmac
import base64
import httpx


def generate_signature(payload: bytes, secret: str) -> str:
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return base64.b64encode(expected).decode()


def test_orders_fulfilled_webhook():
    payload = {
        "id": "123456789",
        "customer": {
            "id": "987654321",
            "email": "test@example.com",
            "first_name": "Test",
            "last_name": "Customer",
        },
        "line_items": [
            {
                "id": "111",
                "product_id": "1",
                "variant_id": "1",
                "title": "Test Product",
                "quantity": 1,
                "price": "49.99",
            }
        ],
        "currency": "USD",
    }

    body = json.dumps(payload).encode()
    secret = "test-webhook-secret"
    signature = generate_signature(body, secret)

    response = httpx.post(
        "http://localhost:8000/webhooks/shopify/1/orders-fulfilled",
        content=body,
        headers={
            "Content-Type": "application/json",
            "x-shopify-hmac-sha256": signature,
        },
        timeout=30.0,
    )

    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")


if __name__ == "__main__":
    print("Testing Shopify webhook endpoint...")
    print("Make sure the server is running: uvicorn app.main:app --reload")
    print()
    test_orders_fulfilled_webhook()
