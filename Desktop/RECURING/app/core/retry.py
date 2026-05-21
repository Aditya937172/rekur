from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any

import httpx
import requests

from app.core.config import AppSettings, load_settings
from app.core.observability import log_pipeline_event


RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


class ExternalAPIRetryError(RuntimeError):
    pass


def requests_request_with_retries(
    method: str,
    url: str,
    *,
    provider: str,
    operation: str,
    settings: AppSettings | None = None,
    request_func: Callable[..., requests.Response] | None = None,
    max_attempts: int | None = None,
    base_delay_seconds: float | None = None,
    **kwargs: Any,
) -> requests.Response:
    settings = settings or load_settings()
    attempts = max(1, max_attempts or settings.external_retry_max_attempts)
    base_delay = base_delay_seconds or settings.external_retry_base_delay_seconds
    requester = request_func or requests.request
    last_exc: BaseException | None = None
    response: requests.Response | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = requester(method, url, **kwargs)
            if response.status_code not in RETRYABLE_STATUS_CODES or attempt >= attempts:
                return response
            log_pipeline_event(
                "external_api_retry",
                logger=logging.getLogger(__name__),
                provider=provider,
                operation=operation,
                status_code=response.status_code,
                attempt=attempt,
                max_attempts=attempts,
            )
            sleep_for_retry(attempt, base_delay, response.headers.get("Retry-After"))
        except requests.RequestException as exc:
            last_exc = exc
            if attempt >= attempts:
                raise ExternalAPIRetryError(
                    f"{provider} {operation} failed after {attempts} attempts: {exc}"
                ) from exc
            log_pipeline_event(
                "external_api_retry",
                logger=logging.getLogger(__name__),
                provider=provider,
                operation=operation,
                error_type=exc.__class__.__name__,
                attempt=attempt,
                max_attempts=attempts,
            )
            sleep_for_retry(attempt, base_delay)

    raise ExternalAPIRetryError(
        f"{provider} {operation} failed after retries: {last_exc or response}"
    )


def httpx_request_with_retries(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    provider: str,
    operation: str,
    settings: AppSettings | None = None,
    max_attempts: int | None = None,
    base_delay_seconds: float | None = None,
    **kwargs: Any,
) -> httpx.Response:
    settings = settings or load_settings()
    attempts = max(1, max_attempts or settings.external_retry_max_attempts)
    base_delay = base_delay_seconds or settings.external_retry_base_delay_seconds
    last_exc: BaseException | None = None
    response: httpx.Response | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = client.request(method, url, **kwargs)
            if response.status_code not in RETRYABLE_STATUS_CODES or attempt >= attempts:
                return response
            log_pipeline_event(
                "external_api_retry",
                logger=logging.getLogger(__name__),
                provider=provider,
                operation=operation,
                status_code=response.status_code,
                attempt=attempt,
                max_attempts=attempts,
            )
            sleep_for_retry(attempt, base_delay, response.headers.get("Retry-After"))
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt >= attempts:
                raise ExternalAPIRetryError(
                    f"{provider} {operation} failed after {attempts} attempts: {exc}"
                ) from exc
            log_pipeline_event(
                "external_api_retry",
                logger=logging.getLogger(__name__),
                provider=provider,
                operation=operation,
                error_type=exc.__class__.__name__,
                attempt=attempt,
                max_attempts=attempts,
            )
            sleep_for_retry(attempt, base_delay)

    raise ExternalAPIRetryError(
        f"{provider} {operation} failed after retries: {last_exc or response}"
    )


def sleep_for_retry(
    attempt: int,
    base_delay_seconds: float,
    retry_after: str | None = None,
) -> None:
    if retry_after:
        try:
            time.sleep(min(float(retry_after), 30.0))
            return
        except ValueError:
            pass
    jitter = random.uniform(0, 0.25)
    time.sleep(min(base_delay_seconds * (2 ** (attempt - 1)) + jitter, 10.0))
