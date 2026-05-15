from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Iterable

from app.core.config import AppSettings, load_settings


@dataclass(frozen=True)
class ProductSignal:
    product_id: int
    title: str
    tags: str | None = None
    image_url: str | None = None


class FashionClipService:
    """Embedding boundary for outfit intelligence.

    Production can replace the local hash fallback with a FashionCLIP model or
    embedding microservice without changing recommendation/campaign code.
    """

    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or load_settings()
        self.dimensions = max(self.settings.fashion_clip_embedding_dimensions, 32)

    def embed_text(self, text: str) -> list[float]:
        return normalized_hash_embedding(text, self.dimensions)

    def embed_product(self, product: ProductSignal) -> list[float]:
        text = f"{product.title} {product.tags or ''} {product.image_url or ''}"
        return self.embed_text(text)

    def embed_product_combination(self, products: Iterable[ProductSignal]) -> list[float]:
        vectors = [self.embed_product(product) for product in products]
        if not vectors:
            return self.embed_text("")
        summed = [sum(values) for values in zip(*vectors)]
        return normalize_vector(summed)

    def compatibility_score(
        self,
        anchor: ProductSignal,
        candidate: ProductSignal,
    ) -> float:
        return cosine_similarity(self.embed_product(anchor), self.embed_product(candidate))


def normalized_hash_embedding(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    tokens = tokenize(text)
    if not tokens:
        tokens = ["empty"]
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        for offset in range(0, len(digest), 4):
            raw = int.from_bytes(digest[offset : offset + 4], "big", signed=False)
            index = raw % dimensions
            sign = 1.0 if raw & 1 else -1.0
            vector[index] += sign
    return normalize_vector(vector)


def tokenize(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", text.lower().replace("_", " "))
        if len(token) > 1
    ]


def normalize_vector(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [value / magnitude for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    return sum(left[index] * right[index] for index in range(size))
