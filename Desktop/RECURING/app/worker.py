from __future__ import annotations

import logging

from celery import Celery, signals

from app.core.config import load_settings
from app.core.observability import capture_exception, log_pipeline_event
from app.services.dead_letter_service import record_dead_letter

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
    task_send_sent_event=True,
    worker_send_task_events=True,
    task_default_queue="retention",
    task_create_missing_queues=True,
)


@signals.task_failure.connect
def handle_task_failure(
    sender=None,
    task_id=None,
    exception=None,
    args=None,
    kwargs=None,
    traceback=None,
    einfo=None,
    **_: object,
) -> None:
    task_name = getattr(sender, "name", None) or str(sender)
    payload = record_dead_letter(
        source="celery_task_failure",
        task_id=task_id,
        task_name=task_name,
        args=args,
        kwargs=kwargs,
        error=str(exception),
        traceback=str(einfo or traceback or ""),
    )
    log_pipeline_event(
        "celery_task_dead_lettered",
        task_id=task_id,
        task_name=task_name,
        dead_letter_recorded_at=payload.get("recorded_at"),
        error=str(exception),
    )
    if exception:
        capture_exception(exception, task_id=task_id, task_name=task_name)
