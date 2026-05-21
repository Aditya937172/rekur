from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    auth_router,
    buyer_memory_router,
    email_events_router,
    events_router,
    gmail_router,
    intent_router,
    internal_router,
    messages_router,
    orders_router,
    outfits_router,
    recommendations_router,
    replies_router,
    retention_router,
    stores_router,
    webhooks_router,
)
from app.core.config import load_settings
from app.core.rate_limit import build_rate_limit_middleware
from app.core.auth import get_current_user
from app.db.session import init_db
from app.models import AppUser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

SENTRY_DSN = os.getenv("SENTRY_DSN")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")


def parse_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[
            FastApiIntegration(),
            SqlalchemyIntegration(),
        ],
        traces_sample_rate=0.1,
        environment=ENVIRONMENT,
    )
    logger.info(f"Sentry initialized for {ENVIRONMENT}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = load_settings()
    init_db()
    logger.info(
        f"Database initialized: {settings.database_url.split('@')[-1] if '@' in settings.database_url else 'local'}"
    )

    if parse_bool(os.getenv("APP_DISABLE_SCHEDULER")):
        logger.info("Scheduler disabled by APP_DISABLE_SCHEDULER")
    else:
        from app.scheduler.cron_scheduler import start_scheduler

        try:
            await start_scheduler()
            logger.info("Scheduler started")
        except Exception as e:
            logger.warning(f"Scheduler not started: {e}")

    yield

    from app.scheduler.cron_scheduler import shutdown_scheduler

    shutdown_scheduler()
    logger.info("Application shutdown complete")


app = FastAPI(
    title="RECURING - Shopify Retention Platform",
    description="AI-powered customer retention and styling platform for Shopify brands",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(build_rate_limit_middleware(load_settings()))


@app.get("/health")
async def health_check() -> dict:
    from app.db.session import SessionLocal
    from app.core.config import load_settings
    from sqlalchemy import text

    db_status = "ok"
    redis_status = "ok"

    db = None
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"error: {str(e)[:50]}"
    finally:
        if db is not None:
            db.close()

    try:
        import redis as redis_client

        settings = load_settings()
        r = redis_client.from_url(settings.redis_url)
        r.ping()
    except Exception as e:
        redis_status = f"error: {str(e)[:50]}"

    return {
        "status": "ok" if db_status == "ok" and redis_status == "ok" else "degraded",
        "version": "0.1.0",
        "environment": ENVIRONMENT,
        "services": {
            "database": db_status,
            "redis": redis_status,
        },
    }


@app.get("/")
async def root() -> dict:
    return {
        "name": "RECURING",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/scheduler/jobs")
async def get_scheduler_jobs() -> list[dict]:
    from app.scheduler.cron_scheduler import get_scheduled_jobs

    return get_scheduled_jobs()


@app.get("/outfits/cache-stats")
async def get_cache_stats() -> dict:
    from sqlalchemy import func
    from app.db.session import SessionLocal
    from app.models import OutfitImageCache

    db = SessionLocal()
    try:
        total = db.query(func.count(OutfitImageCache.id)).scalar() or 0
        total_hits = db.query(func.sum(OutfitImageCache.hit_count)).scalar() or 0

        if total > 0:
            hit_rate = total_hits / (total_hits + total * 0.1)
        else:
            hit_rate = 0.0

        return {
            "total_entries": total,
            "total_hits": total_hits,
            "hit_rate": hit_rate,
        }
    finally:
        db.close()


@app.get("/celery/status")
async def get_celery_status() -> dict:
    from app.worker import celery_app

    try:
        inspect = celery_app.control.inspect()
        stats = inspect.stats()

        if stats:
            workers_online = len(stats)
            active = inspect.active()
            active_count = sum(len(tasks) for tasks in (active or {}).values())
        else:
            workers_online = 0
            active_count = 0

        return {
            "workers_online": workers_online,
            "active_tasks": active_count,
        }
    except Exception as e:
        return {"workers_online": 0, "active_tasks": 0, "error": str(e)[:50]}


@app.get("/celery/dead-letters")
async def get_celery_dead_letters(
    limit: int = Query(default=100, ge=1, le=500),
    _current_user: AppUser = Depends(get_current_user),
) -> list[dict]:
    from app.services.dead_letter_service import list_dead_letters

    return list_dead_letters(limit=limit)


@app.post("/celery/dead-letters/{dead_letter_id}/requeue")
async def requeue_celery_dead_letter(
    dead_letter_id: str,
    _current_user: AppUser = Depends(get_current_user),
) -> dict:
    from app.services.dead_letter_service import get_dead_letter
    from app.worker import celery_app

    row = get_dead_letter(dead_letter_id)
    if not row:
        return {"status": "not_found", "dead_letter_id": dead_letter_id}
    task_name = row.get("task_name")
    if not task_name:
        return {"status": "not_requeued", "reason": "missing task_name"}
    result = celery_app.send_task(
        task_name,
        args=row.get("args") or [],
        kwargs=row.get("kwargs") or {},
    )
    return {
        "status": "requeued",
        "dead_letter_id": dead_letter_id,
        "task_name": task_name,
        "new_task_id": result.id,
    }


app.include_router(stores_router)
app.include_router(auth_router)
app.include_router(buyer_memory_router)
app.include_router(email_events_router)
app.include_router(events_router)
app.include_router(gmail_router)
app.include_router(intent_router)
app.include_router(internal_router)
app.include_router(recommendations_router)
app.include_router(messages_router)
app.include_router(orders_router)
app.include_router(outfits_router)
app.include_router(replies_router)
app.include_router(retention_router)
app.include_router(webhooks_router)

public_dir = Path(__file__).resolve().parents[1] / "public"
if public_dir.exists():
    app.mount("/public", StaticFiles(directory=public_dir), name="public")
