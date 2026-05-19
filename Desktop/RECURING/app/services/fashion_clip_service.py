from __future__ import annotations

import hashlib
import logging
import math
import re
from dataclasses import dataclass
from typing import Iterable

from app.core.config import AppSettings, load_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProductSignal:
    product_id: int
    title: str
    tags: str | None = None
    image_url: str | None = None


_FASHION_VOCAB: dict[str, list[float]] = {}
_EMBEDDING_CACHE: dict[str, list[float]] = {}

CATEGORY_KEYWORDS = {
    "shirt": ["shirt", "blouse", "button-down", "oxford", "linen shirt"],
    "tshirt": ["t-shirt", "tee", "tshirt", "polo", "henley"],
    "jeans": ["jeans", "denim", "blue jeans", "black jeans"],
    "trousers": ["trousers", "pants", "chinos", "slacks", "formal pants"],
    "jacket": ["jacket", "blazer", "bomber", "denim jacket", "leather jacket"],
    "hoodie": ["hoodie", "sweatshirt", "pullover", "zip-up"],
    "dress": ["dress", "gown", "maxi dress", "mini dress", "sundress"],
    "skirt": ["skirt", "mini skirt", "maxi skirt", "pleated"],
    "shorts": ["shorts", "bermuda", "cargo shorts"],
    "cargos": ["cargo", "cargo pants", "cargo trousers"],
    "ethnic": ["ethnic", "kurta", "saree", "lehenga", "traditional"],
    "accessories": ["accessories", "belt", "scarf", "hat", "watch", "bag"],
}

STYLE_KEYWORDS = {
    "casual": ["casual", "relaxed", "everyday", "weekend", "laid-back"],
    "formal": ["formal", "business", "office", "professional", "suit"],
    "streetwear": ["streetwear", "urban", "hypebeast", "street", "hip-hop"],
    "minimal": ["minimal", "clean", "simple", "basic", "essential"],
    "vintage": ["vintage", "retro", "throwback", "classic", "old-school"],
    "bohemian": ["bohemian", "boho", "hippie", "free-spirit", "artisanal"],
    "sporty": ["sporty", "athletic", "gym", "activewear", "workout"],
    "elegant": ["elegant", "sophisticated", "refined", "chic", "polished"],
}

COLOR_MAP = {
    "black": 0.1,
    "white": 0.2,
    "gray": 0.3,
    "grey": 0.3,
    "blue": 0.4,
    "navy": 0.42,
    "red": 0.5,
    "green": 0.6,
    "yellow": 0.7,
    "orange": 0.75,
    "pink": 0.8,
    "purple": 0.85,
    "brown": 0.9,
    "beige": 0.92,
    "cream": 0.93,
    "tan": 0.95,
}

FABRIC_KEYWORDS = {
    "cotton": 1.0,
    "linen": 0.95,
    "denim": 0.9,
    "silk": 0.85,
    "wool": 0.8,
    "polyester": 0.7,
    "leather": 0.75,
    "suede": 0.73,
}


def _generate_fashion_embedding(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    text_lower = text.lower()

    cat_values = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                cat_values[cat] = cat_values.get(cat, 0) + 0.3

    for i, (cat, val) in enumerate(sorted(cat_values.items())):
        idx = i * 10 % dimensions
        vector[idx] = min(val, 1.0)

    style_values = {}
    for style, keywords in STYLE_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                style_values[style] = style_values.get(style, 0) + 0.25

    for i, (style, val) in enumerate(sorted(style_values.items())):
        idx = 50 + (i * 8) % (dimensions - 50)
        vector[idx] = min(val, 1.0)

    for color, base_val in COLOR_MAP.items():
        if color in text_lower:
            idx = int(base_val * 100) % dimensions
            vector[idx] += 0.4

    for fabric, weight in FABRIC_KEYWORDS.items():
        if fabric in text_lower:
            idx = int(weight * 80) % dimensions
            vector[idx] += 0.35

    tokens = tokenize(text)
    for token in tokens:
        token_hash = hashlib.sha256(token.encode("utf-8")).digest()
        for offset in range(0, min(len(token_hash), 16), 4):
            raw = int.from_bytes(token_hash[offset : offset + 4], "big", signed=False)
            index = raw % dimensions
            sign = 1.0 if raw & 1 else -1.0
            vector[index] += sign * 0.1

    return normalize_vector(vector)


def _get_gemini_embedding(text: str, settings: AppSettings) -> list[float]:
    try:
        import google.generativeai as genai

        genai.configure(api_key=settings.gemini_api_key)

        result = genai.embed_content(
            model="models/gemini-embedding-001",
            content=text,
            task_type="retrieval_document",
        )

        if "embedding" in result:
            return result["embedding"]

    except ImportError:
        logger.warning(
            "google-generativeai not installed, falling back to hash embeddings"
        )
    except Exception as e:
        logger.warning(f"Gemini embedding failed: {e}, falling back to hash")

    return None


def get_cached_embedding(
    text: str, dimensions: int, settings: AppSettings = None
) -> list[float]:
    use_gemini = (
        settings
        and settings.gemini_api_key
        and settings.fashion_clip_provider == "gemini_embedding"
    )

    cache_key = f"gemini:{text}" if use_gemini else f"hash:{text}:{dimensions}"
    key_hash = hashlib.md5(cache_key.encode()).hexdigest()

    if key_hash in _EMBEDDING_CACHE:
        return _EMBEDDING_CACHE[key_hash]

    if use_gemini:
        embedding = _get_gemini_embedding(text, settings)
        if embedding:
            if len(embedding) != dimensions:
                embedding = _resize_embedding(embedding, dimensions)
            _EMBEDDING_CACHE[key_hash] = embedding
            return embedding

    embedding = _generate_fashion_embedding(text, dimensions)
    _EMBEDDING_CACHE[key_hash] = embedding
    return embedding


def _resize_embedding(embedding: list[float], target_dim: int) -> list[float]:
    if len(embedding) == target_dim:
        return embedding
    if len(embedding) > target_dim:
        return embedding[:target_dim]
    padding = [0.0] * (target_dim - len(embedding))
    return embedding + padding


class FashionClipService:
    """Semantic embedding service for fashion product matching.

    Supports:
    - Gemini text-embedding-004 (real semantic embeddings)
    - Fashion-aware hash fallback (no API required)

    Set FASHIONCLIP_PROVIDER=gemini_embedding and GEMINI_API_KEY in .env
    """

    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or load_settings()
        self.dimensions = max(self.settings.fashion_clip_embedding_dimensions, 32)
        self._client = None

        if (
            self.settings.fashion_clip_provider == "gemini_embedding"
            and self.settings.gemini_api_key
        ):
            try:
                import google.generativeai as genai

                genai.configure(api_key=self.settings.gemini_api_key)
                self._client = genai
                logger.info("FashionClipService initialized with Gemini embeddings")
            except ImportError:
                logger.warning("google-generativeai not installed, using hash fallback")
            except Exception as e:
                logger.warning(f"Gemini init failed: {e}, using hash fallback")

    def embed_text(self, text: str) -> list[float]:
        return get_cached_embedding(text, self.dimensions, self.settings)

    def embed_product(self, product: ProductSignal) -> list[float]:
        enriched = f"fashion: {product.title}. style: {product.tags or ''}. category: clothing apparel."
        return self.embed_text(enriched)

    def embed_product_combination(
        self, products: Iterable[ProductSignal]
    ) -> list[float]:
        product_list = list(products)
        if not product_list:
            return self.embed_text("")

        texts = []
        for p in product_list:
            texts.append(f"{p.title} ({p.tags or 'style'})")

        combined = " outfit with ".join(texts)
        return self.embed_text(f"complete outfit: {combined}")

    def compatibility_score(
        self, anchor: ProductSignal, candidate: ProductSignal
    ) -> float:
        return cosine_similarity(
            self.embed_product(anchor), self.embed_product(candidate)
        )


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
