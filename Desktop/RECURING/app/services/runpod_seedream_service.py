from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

from app.core.config import AppSettings, load_settings


class RunPodSeedreamError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class SeedreamImageResult:
    image_url: str | None
    image_base64: str | None
    job_id: str | None
    status: str
    raw: dict[str, Any]


def generate_seedream_image(
    *,
    prompt: str,
    settings: AppSettings | None = None,
) -> SeedreamImageResult:
    settings = settings or load_settings()
    ensure_runpod_configured(settings)

    run_response = requests.post(
        f"{settings.runpod_base_url}/{settings.runpod_seedream_endpoint_id}/run",
        headers=runpod_headers(settings),
        json={
            "input": {
                "model": "seedream-v4",
                "prompt": prompt,
                "num_images": 1,
            }
        },
        timeout=settings.runpod_timeout_seconds,
    )
    if run_response.status_code >= 400:
        raise RunPodSeedreamError(
            f"RunPod Seedream request failed with HTTP {run_response.status_code}: {run_response.text[:500]}",
            status_code=502,
        )
    payload = run_response.json()
    job_id = str(payload.get("id") or payload.get("job_id") or "")
    if not job_id:
        return parse_seedream_output(payload, job_id=None)
    return poll_seedream_job(settings=settings, job_id=job_id)


def poll_seedream_job(
    *,
    settings: AppSettings,
    job_id: str,
) -> SeedreamImageResult:
    deadline = time.monotonic() + settings.runpod_max_wait_seconds
    last_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = requests.get(
            f"{settings.runpod_base_url}/{settings.runpod_seedream_endpoint_id}/status/{job_id}",
            headers=runpod_headers(settings),
            timeout=settings.runpod_timeout_seconds,
        )
        if response.status_code >= 400:
            raise RunPodSeedreamError(
                f"RunPod Seedream status failed with HTTP {response.status_code}: {response.text[:500]}",
                status_code=502,
            )
        payload = response.json()
        last_payload = payload
        status = str(payload.get("status") or "").upper()
        if status in {"COMPLETED", "SUCCEEDED"}:
            return parse_seedream_output(payload, job_id=job_id)
        if status in {"FAILED", "CANCELLED", "TIMED_OUT"}:
            raise RunPodSeedreamError(
                f"RunPod Seedream job {job_id} failed: {payload}",
                status_code=502,
            )
        time.sleep(settings.runpod_poll_interval_seconds)
    raise RunPodSeedreamError(
        f"RunPod Seedream job {job_id} did not finish in time: {last_payload}",
        status_code=504,
    )


def parse_seedream_output(payload: dict[str, Any], *, job_id: str | None) -> SeedreamImageResult:
    output = payload.get("output") or payload
    image_url = None
    image_base64 = None
    if isinstance(output, dict):
        image_url = (
            output.get("image_url")
            or output.get("url")
            or output.get("image")
            or first_value(output.get("images"))
        )
        image_base64 = output.get("image_base64") or output.get("base64")
    elif isinstance(output, list):
        image_url = first_value(output)
    return SeedreamImageResult(
        image_url=str(image_url) if image_url else None,
        image_base64=str(image_base64) if image_base64 else None,
        job_id=job_id,
        status=str(payload.get("status") or "completed").lower(),
        raw=payload,
    )


def first_value(value: Any) -> Any:
    if isinstance(value, list) and value:
        item = value[0]
        if isinstance(item, dict):
            return item.get("url") or item.get("image_url") or item.get("image")
        return item
    return None


def ensure_runpod_configured(settings: AppSettings) -> None:
    if not settings.runpod_api_key or not settings.runpod_seedream_endpoint_id:
        raise RunPodSeedreamError(
            "RUNPOD_API_KEY and RUNPOD_SEEDREAM_ENDPOINT_ID are required for Seedream V4 image generation.",
            status_code=400,
        )


def runpod_headers(settings: AppSettings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.runpod_api_key}",
        "Content-Type": "application/json",
    }
