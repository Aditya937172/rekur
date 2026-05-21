from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Any

import requests

from app.core.config import AppSettings, load_settings
from app.core.observability import log_pipeline_event
from app.core.retry import ExternalAPIRetryError, requests_request_with_retries


class ImageGenerationServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ImageGenerationResult:
    task_id: str | None
    task_status: str | None
    task_progress: int | None
    image_url: str | None
    image_base64: str | None
    credits_reserved: float | None
    credits_used: float | None
    usage: dict[str, Any]
    raw_response: dict[str, Any]


def generate_outfit_image(
    *,
    prompt: str,
    image_urls: list[str] | None = None,
    settings: AppSettings | None = None,
) -> ImageGenerationResult:
    settings = settings or load_settings()
    if not settings.image_api_key:
        raise ImageGenerationServiceError(
            "GPT_IMAGE_KEY is missing. Add GPT_IMAGE_KEY to .env and set IMAGE_PROVIDER=evolink for local outfit image generation.",
            status_code=400,
        )

    allowed_image_urls = budget_image_urls(image_urls or [], settings)
    prompt_for_generation = budget_prompt(prompt, has_reference_images=bool(allowed_image_urls))

    payload: dict[str, Any] = {
        "model": settings.image_model,
        "prompt": prompt_for_generation,
        "size": settings.image_size,
        "resolution": settings.image_resolution,
        "quality": budget_quality(settings),
        "n": 1,
    }
    if allowed_image_urls:
        payload["image_urls"] = allowed_image_urls

    task = create_image_task(payload=payload, settings=settings)
    enforce_task_credit_cap(task, settings)
    task_id = str(task.get("id") or "")
    if not task_id:
        raise ImageGenerationServiceError(
            "Image API response did not include a task id.",
            status_code=502,
        )
    data = wait_for_image_task(task_id=task_id, settings=settings)
    image_url, image_base64 = parse_image_response(data)
    if not image_url and not image_base64:
        raise ImageGenerationServiceError(
            "Image API response did not include an image URL or base64 image.",
            status_code=502,
        )
    return ImageGenerationResult(
        task_id=task_id,
        task_status=str(data.get("status") or ""),
        task_progress=safe_int(data.get("progress")),
        image_url=image_url,
        image_base64=image_base64,
        credits_reserved=extract_credits_reserved(task),
        credits_used=extract_credits_used(data),
        usage=image_usage(data),
        raw_response=data,
    )


def create_image_task(
    *,
    payload: dict[str, Any],
    settings: AppSettings,
) -> dict[str, Any]:
    try:
        response = requests_request_with_retries(
            "POST",
            settings.image_api_base_url,
            provider="evolink",
            operation="create_image_task",
            settings=settings,
            headers=image_api_headers(settings),
            json=payload,
            timeout=settings.image_timeout_seconds,
        )
    except ExternalAPIRetryError as exc:
        raise ImageGenerationServiceError(str(exc), status_code=502) from exc
    if response.status_code >= 400:
        raise ImageGenerationServiceError(
            image_api_error_message(response, settings=settings),
            status_code=502,
        )
    try:
        task = response.json()
    except ValueError as exc:
        raise ImageGenerationServiceError(
            "Image API returned invalid JSON while creating task.",
            status_code=502,
        ) from exc
    log_pipeline_event(
        "image_task_created",
        provider="evolink",
        task_id=task.get("id"),
        status=task.get("status"),
    )
    return task


def get_image_task(
    *,
    task_id: str,
    settings: AppSettings,
) -> dict[str, Any]:
    try:
        response = requests_request_with_retries(
            "GET",
            f"{settings.image_task_base_url.rstrip('/')}/{task_id}",
            provider="evolink",
            operation="get_image_task",
            settings=settings,
            headers=image_api_headers(settings),
            timeout=settings.image_timeout_seconds,
        )
    except ExternalAPIRetryError as exc:
        raise ImageGenerationServiceError(str(exc), status_code=502) from exc
    if response.status_code >= 400:
        raise ImageGenerationServiceError(
            image_api_error_message(response, settings=settings),
            status_code=502,
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ImageGenerationServiceError(
            "Image API returned invalid JSON while reading task status.",
            status_code=502,
        ) from exc


def wait_for_image_task(
    *,
    task_id: str,
    settings: AppSettings,
) -> dict[str, Any]:
    deadline = time.monotonic() + settings.image_max_wait_seconds
    last_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        payload = get_image_task(task_id=task_id, settings=settings)
        last_payload = payload
        status = str(payload.get("status") or "").lower()
        if status == "completed":
            return payload
        if status in {"failed", "cancelled", "canceled"}:
            raise ImageGenerationServiceError(
                f"Image task {task_id} failed: {task_error_message(payload)}",
                status_code=502,
            )
        time.sleep(settings.image_poll_interval_seconds)

    raise ImageGenerationServiceError(
        f"Image task {task_id} did not complete within {settings.image_max_wait_seconds} seconds. Last status: {last_payload.get('status')}.",
        status_code=504,
    )


def parse_image_response(data: dict[str, Any]) -> tuple[str | None, str | None]:
    image_url = None
    image_base64 = None

    if isinstance(data.get("image_urls"), list) and data["image_urls"]:
        image_url = str(data["image_urls"][0])

    if isinstance(data.get("results"), list) and data["results"]:
        image_url = str(data["results"][0])

    items = data.get("data")
    if isinstance(items, list) and items:
        first = items[0] or {}
        if isinstance(first, dict):
            image_url = image_url or first.get("url")
            image_base64 = first.get("b64_json") or first.get("base64")

    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"image", "output_image"}:
                image_url = image_url or item.get("url")
                image_base64 = image_base64 or item.get("b64_json") or item.get("base64")

    return image_url, image_base64


def image_api_headers(settings: AppSettings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.image_api_key}",
        "Content-Type": "application/json",
    }


def budget_quality(settings: AppSettings) -> str:
    if settings.image_max_credits_per_task <= 1.2:
        return "low"
    return settings.image_quality


def budget_image_urls(
    image_urls: list[str],
    settings: AppSettings,
) -> list[str]:
    max_reference_urls = max(settings.image_max_reference_urls, 0)
    if not image_urls or max_reference_urls == 0:
        if image_urls:
            log_pipeline_event(
                "image_references_dropped_for_budget",
                provider="evolink",
                requested_reference_count=len(image_urls),
                max_credits_per_task=settings.image_max_credits_per_task,
            )
        return []
    return image_urls[:max_reference_urls]


def budget_prompt(prompt: str, *, has_reference_images: bool) -> str:
    if has_reference_images:
        return prompt
    return prompt.replace(
        (
            "Use the attached product reference images as visual anchors. "
            "Match the exact colors, fabrics, and styles shown in those images. "
        ),
        (
            "Use the product names, tags, colors, and customer style memory "
            "as visual anchors. "
        ),
    )


def enforce_task_credit_cap(task: dict[str, Any], settings: AppSettings) -> None:
    max_credits = settings.image_max_credits_per_task
    if max_credits <= 0:
        return
    reserved = extract_credits_reserved(task)
    task_id = str(task.get("id") or "")
    if reserved is None:
        if task_id:
            cancel_image_task(task_id=task_id, settings=settings)
        raise ImageGenerationServiceError(
            (
                "Image generation blocked by credit cap because the provider "
                "did not report usage. Cannot prove the task is under "
                f"IMAGE_MAX_CREDITS_PER_TASK={max_credits:.4f}."
            ),
            status_code=502,
        )
    if reserved <= max_credits:
        return
    if task_id:
        cancel_image_task(task_id=task_id, settings=settings)
    raise ImageGenerationServiceError(
        (
            "Image generation blocked by credit cap. "
            f"Provider reserved {reserved:.4f} credits but IMAGE_MAX_CREDITS_PER_TASK="
            f"{max_credits:.4f}. Lower IMAGE_SIZE/IMAGE_QUALITY, keep "
            "IMAGE_MAX_REFERENCE_URLS=0, or increase provider credits intentionally."
        ),
        status_code=402,
    )


def extract_credits_reserved(payload: dict[str, Any]) -> float | None:
    usage = payload.get("usage")
    if isinstance(usage, dict):
        for key in ("credits_reserved", "credits", "cost", "estimated_cost"):
            value = usage.get(key)
            parsed = safe_float(value)
            if parsed is not None:
                return parsed
    for key in ("credits_reserved", "credits", "cost", "estimated_cost"):
        parsed = safe_float(payload.get(key))
        if parsed is not None:
            return parsed
    return None


def extract_credits_used(payload: dict[str, Any]) -> float | None:
    usage = payload.get("usage")
    if isinstance(usage, dict):
        for key in ("credits_used", "credits_charged", "credits", "cost"):
            parsed = safe_float(usage.get(key))
            if parsed is not None:
                return parsed
    for key in ("credits_used", "credits_charged", "credits", "cost"):
        parsed = safe_float(payload.get(key))
        if parsed is not None:
            return parsed
    return None


def image_usage(payload: dict[str, Any]) -> dict[str, Any]:
    usage = payload.get("usage")
    if isinstance(usage, dict):
        return usage
    return {}


def cancel_image_task(*, task_id: str, settings: AppSettings) -> None:
    # Best effort: providers do not always support cancellation for failed
    # pre-deduction attempts, but this prevents over-budget successful tasks
    # from continuing when a cancel endpoint is available.
    try:
        response = requests_request_with_retries(
            "POST",
            f"{settings.image_task_base_url.rstrip('/')}/{task_id}/cancel",
            provider="evolink",
            operation="cancel_image_task",
            settings=settings,
            headers=image_api_headers(settings),
            timeout=settings.image_timeout_seconds,
        )
        log_pipeline_event(
            "image_task_cancel_requested",
            provider="evolink",
            task_id=task_id,
            status_code=response.status_code,
        )
    except Exception as exc:  # pragma: no cover - best effort only
        log_pipeline_event(
            "image_task_cancel_failed",
            provider="evolink",
            task_id=task_id,
            error=str(exc)[:300],
        )


def task_error_message(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or payload)


def safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def image_api_error_message(
    response: requests.Response,
    *,
    settings: AppSettings | None = None,
) -> str:
    body = response.text.strip()
    body = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-***", body)
    if len(body) > 500:
        body = body[:500] + "..."
    suffix = ""
    if settings is not None:
        needed = credits_needed_from_error(body)
        if needed is not None and needed > settings.image_max_credits_per_task:
            suffix = (
                " Request exceeds IMAGE_MAX_CREDITS_PER_TASK="
                f"{settings.image_max_credits_per_task:.4f}; not retrying above budget."
            )
    return f"Image generation failed with HTTP {response.status_code}: {body}{suffix}"


def credits_needed_from_error(body: str) -> float | None:
    match = re.search(r"need\s+([0-9]+(?:\.[0-9]+)?)\s+credits", body, re.IGNORECASE)
    if not match:
        return None
    return safe_float(match.group(1))
