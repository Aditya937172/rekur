from __future__ import annotations

import time
from typing import Any, Callable

import requests
from sqlalchemy.orm import Session

from app.core.config import AppSettings, load_settings
from app.models import Customer
from app.schemas import CustomerMessage, CustomerRecommendations, ProductRecommendation
from app.services.recommendation_engine import (
    RecommendationEngineError,
    get_recommendations_for_customer,
    get_recommendations_for_customers,
)


ELIGIBLE_INTENTS = {"high", "medium"}
GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


class MessageEngineError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def generate_messages_for_customers(
    db: Session,
    store_id: int,
    *,
    limit: int = 5000,
    settings: AppSettings | None = None,
    post_func: Callable[..., requests.Response] | None = None,
) -> list[CustomerMessage]:
    settings = settings or load_settings()
    ensure_groq_configured(settings)

    try:
        recommendation_rows = get_recommendations_for_customers(
            db,
            store_id,
            customer_limit=5000,
            product_limit=1,
        )
    except RecommendationEngineError as exc:
        raise MessageEngineError(str(exc), status_code=404) from exc

    customer_ids = [
        row.customer_id
        for row in recommendation_rows
        if row.intent in ELIGIBLE_INTENTS and row.recommendations
    ][:limit]
    customers = load_customers_by_id(db, customer_ids)

    messages: list[CustomerMessage] = []
    for row in recommendation_rows:
        if len(messages) >= limit:
            break
        if row.intent not in ELIGIBLE_INTENTS or not row.recommendations:
            continue
        customer = customers.get(row.customer_id)
        if not customer:
            continue
        messages.append(
            build_customer_message(
                settings=settings,
                customer=customer,
                recommendation_row=row,
                product=row.recommendations[0],
                post_func=post_func,
            )
        )

    return messages


def generate_message_for_customer(
    db: Session,
    store_id: int,
    customer_id: int,
    *,
    settings: AppSettings | None = None,
    post_func: Callable[..., requests.Response] | None = None,
) -> CustomerMessage:
    settings = settings or load_settings()
    ensure_groq_configured(settings)

    customer = db.get(Customer, customer_id)
    if not customer or customer.store_id != store_id:
        raise MessageEngineError(f"Customer {customer_id} was not found.", status_code=404)

    try:
        recommendation_row = get_recommendations_for_customer(
            db,
            store_id,
            customer_id,
            product_limit=1,
        )
    except RecommendationEngineError as exc:
        raise MessageEngineError(str(exc), status_code=404) from exc

    if recommendation_row.intent not in ELIGIBLE_INTENTS:
        raise MessageEngineError(
            "Messages are generated only for high or medium intent customers.",
            status_code=400,
        )
    if not recommendation_row.recommendations:
        raise MessageEngineError(
            f"No recommendation found for customer {customer_id}.",
            status_code=404,
        )

    return build_customer_message(
        settings=settings,
        customer=customer,
        recommendation_row=recommendation_row,
        product=recommendation_row.recommendations[0],
        post_func=post_func,
    )


def build_customer_message(
    *,
    settings: AppSettings,
    customer: Customer,
    recommendation_row: CustomerRecommendations,
    product: ProductRecommendation,
    post_func: Callable[..., requests.Response] | None = None,
) -> CustomerMessage:
    customer_name = display_name(customer)
    prompt = build_prompt(
        name=customer_name,
        intent=recommendation_row.intent,
        product_title=product.title,
        reason=product.reason,
    )
    message = call_groq(
        settings=settings,
        prompt=prompt,
        post_func=post_func,
    )
    return CustomerMessage(
        customer_id=customer.id,
        customer_name=customer_name,
        intent=recommendation_row.intent,
        score=recommendation_row.score,
        product_id=product.product_id,
        product_title=product.title,
        recommendation_reason=product.reason,
        message=message,
    )


def build_prompt(
    *,
    name: str,
    intent: str,
    product_title: str,
    reason: str,
) -> str:
    return (
        "Write a short (max 2 sentences) personalized ecommerce message.\n\n"
        f"Customer: {name}\n"
        f"Intent: {intent}\n"
        f"Product: {product_title}\n"
        f"Reason: {reason}\n\n"
        "Make it natural, not spammy, and slightly persuasive.\n"
        "Do not use emojis.\n"
        "Output only the message."
    )


def call_groq(
    *,
    settings: AppSettings,
    prompt: str,
    post_func: Callable[..., requests.Response] | None = None,
) -> str:
    ensure_groq_configured(settings)
    post = post_func or requests.post
    max_retries = max(settings.groq_max_retries, 0)
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            response = post(
                settings.groq_base_url,
                headers={
                    "Authorization": f"Bearer {settings.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.groq_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                },
                timeout=settings.groq_timeout_seconds,
            )
            if should_retry(response.status_code) and attempt < max_retries:
                sleep_before_retry(attempt)
                continue
            if response.status_code >= 400:
                raise MessageEngineError(
                    groq_error_message(response),
                    status_code=502,
                )
            return parse_groq_message(response.json())
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if attempt < max_retries:
                sleep_before_retry(attempt)
                continue
            raise MessageEngineError(
                "Groq request failed after retries.",
                status_code=502,
            ) from exc
        except ValueError as exc:
            raise MessageEngineError(
                "Groq returned an invalid JSON response.",
                status_code=502,
            ) from exc

    raise MessageEngineError(
        f"Groq request failed: {last_error}",
        status_code=502,
    )


def parse_groq_message(payload: dict[str, Any]) -> str:
    try:
        message = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise MessageEngineError(
            "Groq response did not include a message.",
            status_code=502,
        ) from exc

    cleaned = str(message).strip()
    if (
        len(cleaned) >= 2
        and cleaned[0] == cleaned[-1]
        and cleaned[0] in {"'", '"'}
    ):
        cleaned = cleaned[1:-1].strip()
    if not cleaned:
        raise MessageEngineError("Groq returned an empty message.", status_code=502)
    return cleaned


def should_retry(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def sleep_before_retry(attempt: int) -> None:
    time.sleep(min(2**attempt, 4))


def groq_error_message(response: requests.Response) -> str:
    body = response.text.strip()
    if len(body) > 300:
        body = body[:300] + "..."
    return f"Groq request failed with HTTP {response.status_code}: {body}"


def ensure_groq_configured(settings: AppSettings) -> None:
    if not settings.groq_api_key:
        raise MessageEngineError(
            "GROQ_API_KEY is missing. Add it to .env before generating messages.",
            status_code=400,
        )


def load_customers_by_id(db: Session, customer_ids: list[int]) -> dict[int, Customer]:
    if not customer_ids:
        return {}
    customers = db.query(Customer).filter(Customer.id.in_(customer_ids)).all()
    return {customer.id: customer for customer in customers}


def display_name(customer: Customer) -> str:
    name = " ".join(
        part for part in [customer.first_name, customer.last_name] if part
    ).strip()
    return name or customer.email or f"Customer {customer.id}"
