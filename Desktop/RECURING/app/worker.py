from __future__ import annotations

import logging

from celery import Celery

from app.core.config import load_settings

logger = logging.getLogger(__name__)
settings = load_settings()

celery_app = Celery(
    "rekur",
    broker=settings.redis_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.outfit_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_max_retries=3,
    task_default_retry_delay=60,
    broker_connection_retry_on_startup=True,
)
