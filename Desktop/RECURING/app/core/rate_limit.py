from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.responses import JSONResponse

from app.core.config import AppSettings


PUBLIC_RATE_LIMIT_PATHS = (
    "/events",
    "/webhooks/",
    "/email-events/sendgrid",
    "/replies/inbound",
    "/gmail/oauth/callback",
    "/connect/shopify/callback",
)


class InMemoryRateLimiter:
    def __init__(self, *, max_requests: int, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> tuple[bool, int, int]:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()
        remaining = max(self.max_requests - len(hits), 0)
        if len(hits) >= self.max_requests:
            retry_after = int(max(self.window_seconds - (now - hits[0]), 1))
            return False, remaining, retry_after
        hits.append(now)
        return True, max(self.max_requests - len(hits), 0), 0


def should_rate_limit(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix) for prefix in PUBLIC_RATE_LIMIT_PATHS)


def client_key(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        ip = forwarded_for.split(",", 1)[0].strip()
    else:
        ip = request.client.host if request.client else "unknown"
    return f"{ip}:{request.url.path}"


def build_rate_limit_middleware(settings: AppSettings) -> Callable:
    limiter = InMemoryRateLimiter(max_requests=settings.public_rate_limit_per_minute)

    async def rate_limit_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if (
            settings.public_rate_limit_enabled
            and settings.public_rate_limit_per_minute > 0
            and should_rate_limit(request.url.path)
        ):
            allowed, remaining, retry_after = limiter.allow(client_key(request))
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Rate limit exceeded. Retry later.",
                        "retry_after_seconds": retry_after,
                    },
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(settings.public_rate_limit_per_minute),
                        "X-RateLimit-Remaining": str(remaining),
                    },
                )
            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(settings.public_rate_limit_per_minute)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            return response
        return await call_next(request)

    return rate_limit_middleware
