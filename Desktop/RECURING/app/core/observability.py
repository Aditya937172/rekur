from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any


SECRET_PATTERNS = (
    re.compile(r"shpat_[A-Za-z0-9_\-]+"),
    re.compile(r"shpss_[A-Za-z0-9_\-]+"),
    re.compile(r"gsk_[A-Za-z0-9_\-]+"),
    re.compile(r"sk-[A-Za-z0-9_\-]+"),
)


def log_pipeline_event(
    event: str,
    *,
    level: int = logging.INFO,
    logger: logging.Logger | None = None,
    **fields: Any,
) -> None:
    target = logger or logging.getLogger("app.pipeline")
    payload = {
        "event": event,
        **{key: sanitize(value) for key, value in fields.items() if value is not None},
    }
    target.log(level, json.dumps(payload, default=json_default, sort_keys=True))


def log_pipeline_error(
    event: str,
    exc: BaseException,
    *,
    logger: logging.Logger | None = None,
    **fields: Any,
) -> None:
    log_pipeline_event(
        event,
        level=logging.ERROR,
        logger=logger,
        error_type=exc.__class__.__name__,
        error_message=str(exc),
        **fields,
    )


def capture_exception(exc: BaseException, **context: Any) -> None:
    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            for key, value in context.items():
                scope.set_extra(key, sanitize(value))
            sentry_sdk.capture_exception(exc)
    except Exception:
        return


def sanitize(value: Any) -> Any:
    if isinstance(value, str):
        cleaned = value
        for pattern in SECRET_PATTERNS:
            cleaned = pattern.sub("***", cleaned)
        return cleaned
    if isinstance(value, dict):
        return {key: sanitize(val) for key, val in value.items() if not is_secret_key(key)}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize(item) for item in value]
    return value


def is_secret_key(key: Any) -> bool:
    lowered = str(key).lower()
    return any(part in lowered for part in ("secret", "token", "authorization", "api_key", "password"))


def json_default(value: Any) -> str | int | float:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)
