"""Test SendGrid email webhook endpoint."""

import json
import httpx


def test_sendgrid_webhook():
    test_events = [
        {
            "email": "test@example.com",
            "event": "delivered",
            "sg_message_id": "test_msg_123",
            "timestamp": 1700000000,
            "campaign_type": "order_delivered_followup",
        },
        {
            "email": "test@example.com",
            "event": "open",
            "sg_message_id": "test_msg_123",
            "timestamp": 1700000100,
        },
        {
            "email": "test@example.com",
            "event": "click",
            "sg_message_id": "test_msg_123",
            "timestamp": 1700000200,
            "url": "https://shop.example.com/products/123",
        },
    ]

    response = httpx.post(
        "http://localhost:8000/email-events/sendgrid",
        json=test_events,
        timeout=10.0,
    )

    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")

    response = httpx.post(
        "http://localhost:8000/email-events/sendgrid/test",
        json=test_events,
        timeout=10.0,
    )

    print(f"Test Status: {response.status_code}")
    print(f"Test Response: {response.json()}")


if __name__ == "__main__":
    print("Testing SendGrid email webhook endpoint...")
    print("Make sure server is running: uvicorn app.main:app --reload")
    print()
    test_sendgrid_webhook()
