from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Any

import requests

from app.core.config import AppSettings, load_settings


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
            "GPT_IMAGE_KEY is missing. Add GPT_IMAGE_KEY to .env before generating outfit images.",
            status_code=400,
        )

    payload: dict[str, Any] = {
        "model": settings.image_model,
        "prompt": prompt,
        "size": settings.image_size,
        "resolution": settings.image_resolution,
        "quality": settings.image_quality,
        "n": 1,
    }
    if image_urls:
        payload["image_urls"] = image_urls

    task = create_image_task(payload=payload, settings=settings)
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
        raw_response=data,
    )


def create_image_task(
    *,
    payload: dict[str, Any],
    settings: AppSettings,
) -> dict[str, Any]:
    response = requests.post(
        settings.image_api_base_url,
        headers=image_api_headers(settings),
        json=payload,
        timeout=settings.image_timeout_seconds,
    )
    if response.status_code >= 400:
        raise ImageGenerationServiceError(
            image_api_error_message(response),
            status_code=502,
        )
    return response.json()


def get_image_task(
    *,
    task_id: str,
    settings: AppSettings,
) -> dict[str, Any]:
    response = requests.get(
        f"{settings.image_task_base_url.rstrip('/')}/{task_id}",
        headers=image_api_headers(settings),
        timeout=settings.image_timeout_seconds,
    )
    if response.status_code >= 400:
        raise ImageGenerationServiceError(
            image_api_error_message(response),
            status_code=502,
        )
    return response.json()


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


def image_api_error_message(response: requests.Response) -> str:
    body = response.text.strip()
    body = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-***", body)
    if len(body) > 500:
        body = body[:500] + "..."
    return f"Image generation failed with HTTP {response.status_code}: {body}"
