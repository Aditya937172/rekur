from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import AppSettings, load_settings
from app.models import OutfitImageCache
from app.services.fashion_clip_service import cosine_similarity


@dataclass(frozen=True)
class CacheLookupResult:
    cache: OutfitImageCache | None
    similarity: float


def search_outfit_image_cache(
    db: Session,
    *,
    store_id: int,
    trigger_reason: str,
    embedding: list[float],
    settings: AppSettings | None = None,
) -> CacheLookupResult:
    settings = settings or load_settings()
    best: OutfitImageCache | None = None
    best_score = 0.0
    rows = db.scalars(
        select(OutfitImageCache).where(
            OutfitImageCache.store_id == store_id,
            OutfitImageCache.trigger_reason == trigger_reason,
        )
    ).all()
    for row in rows:
        score = cosine_similarity(embedding, list(row.embedding_json or []))
        if score > best_score:
            best = row
            best_score = score
    if best and best_score >= settings.vector_cache_threshold:
        best.hit_count += 1
        db.flush()
        return CacheLookupResult(cache=best, similarity=best_score)
    return CacheLookupResult(cache=None, similarity=best_score)


def store_outfit_image_cache(
    db: Session,
    *,
    store_id: int,
    trigger_reason: str,
    cache_key: str,
    product_ids: list[int],
    embedding: list[float],
    image_url: str | None,
    image_base64: str | None,
    provider: str | None,
    model_name: str | None,
    metadata: dict[str, Any] | None = None,
) -> OutfitImageCache:
    cache = OutfitImageCache(
        store_id=store_id,
        trigger_reason=trigger_reason,
        cache_key=cache_key,
        product_ids_json=product_ids,
        embedding_json=embedding,
        image_url=image_url,
        image_base64=image_base64,
        provider=provider,
        model_name=model_name,
        metadata_json=metadata or {},
    )
    db.add(cache)
    db.flush()
    return cache


def product_combination_cache_key(product_ids: list[int]) -> str:
    return "products:" + "-".join(str(product_id) for product_id in sorted(product_ids))
