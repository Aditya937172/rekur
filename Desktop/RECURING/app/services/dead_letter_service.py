from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import load_settings
from app.core.observability import sanitize


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def dead_letter_file() -> Path:
    settings = load_settings()
    settings.dead_letter_dir.mkdir(parents=True, exist_ok=True)
    return settings.dead_letter_dir / "celery_tasks.jsonl"


def record_dead_letter(
    *,
    source: str,
    task_id: str | None,
    task_name: str | None,
    args: Any = None,
    kwargs: Any = None,
    error: str | None = None,
    traceback: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "dead_letter_id": uuid4().hex,
        "recorded_at": utc_now().isoformat(),
        "source": source,
        "task_id": task_id,
        "task_name": task_name,
        "args": sanitize(args),
        "kwargs": sanitize(kwargs),
        "error": sanitize(error),
        "traceback": sanitize(traceback),
        "metadata": sanitize(metadata or {}),
    }
    path = dead_letter_file()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return payload


def list_dead_letters(limit: int = 100) -> list[dict[str, Any]]:
    path = dead_letter_file()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    rows: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(rows))


def get_dead_letter(dead_letter_id: str) -> dict[str, Any] | None:
    for row in list_dead_letters(limit=10000):
        if row.get("dead_letter_id") == dead_letter_id:
            return row
    return None
