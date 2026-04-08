"""
Async Semantic Caching Engine (Krites-Style)
==============================================
Production-grade semantic caching for the G4H-RMA Quant Engine.

Features:
  - Vector embedding-based similarity search (TF-IDF approximation, no external deps)
  - Async-safe concurrent access with asyncio locks
  - Configurable TTL with LRU eviction
  - Cache statistics and hit-rate tracking
  - Bounded memory usage with automatic pruning

Architecture:
  - SemanticCache: Main cache with embedding indexing
  - CachedItem: Individual cache entry with embedding, TTL, access tracking
  - EmbeddingEngine: Lightweight TF-IDF-style text embedding (no ML deps)

Usage:
  from core.semantic_cache import semantic_cache

  @semantic_cache(ttl=3600)
  async def expensive_scan(base, quote):
      # ... expensive computation ...
      return result

  Or manually:
  result = await semantic_cache.get_or_compute(
      "scan:SPY/QQQ:params_hash",
      lambda: expensive_scan("SPY", "QQQ"),
      ttl=3600
  )
"""
from __future__ import annotations
import asyncio
import hashlib
import logging
import math
import re
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from functools import wraps

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────

DEFAULT_TTL = 3600  # 1 hour
DEFAULT_SIMILARITY_THRESHOLD = 0.82
DEFAULT_MAX_ENTRIES = 500
DEFAULT_EVICTION_RATIO = 0.15  # Evict bottom 15% on overflow


@dataclass
class CachedItem:
    """A single cached entry with metadata."""
    key: str
    value: Any
    embedding: List[float]
    created_at: float
    last_accessed: float
    access_count: int
    ttl: int
    size_bytes: int = 0

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
            "ttl": self.ttl,
            "age_seconds": round(self.age_seconds, 1),
            "is_expired": self.is_expired,
            "size_bytes": self.size_bytes,
        }


class EmbeddingEngine:
    """
    Lightweight text embedding using character n-grams + TF-IDF approximation.
    No external ML dependencies — produces fixed-length vectors for similarity search.
    """

    def __init__(self, dim: int = 64):
        self.dim = dim

    def embed(self, text: str) -> List[float]:
        """
        Create a fixed-length embedding from text.
        Uses character trigram hashing into a fixed-dimensional space.
        """
        if not text:
            return [0.0] * self.dim

        text = text.lower().strip()
        vector = [0.0] * self.dim

        # Character trigram features
        trigrams = self._extract_trigrams(text)

        # Hash each trigram into a bucket
        for trigram in trigrams:
            h = self._hash_trigram(trigram)
            bucket = h % self.dim
            vector[bucket] += 1.0

        # L2 normalize
        magnitude = math.sqrt(sum(v * v for v in vector))
        if magnitude > 0:
            vector = [v / magnitude for v in vector]

        return vector

    def _extract_trigrams(self, text: str) -> List[str]:
        """Extract character trigrams from text."""
        # Clean text
        text = re.sub(r'[^a-z0-9\s]', ' ', text)
        tokens = text.split()

        trigrams = []
        for token in tokens:
            if len(token) >= 3:
                for i in range(len(token) - 2):
                    trigrams.append(token[i:i+3])
            else:
                trigrams.append(token)
        return trigrams

    def _hash_trigram(self, trigram: str) -> int:
        """Hash a trigram to an integer."""
        h = 0
        for i, c in enumerate(trigram):
            h = h * 31 + ord(c)
        return abs(h)

    @staticmethod
    def cosine_similarity(a: List[float], b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not a or not b:
            return 0.0

        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))

        if mag_a == 0 or mag_b == 0:
            return 0.0

        return dot / (mag_a * mag_b)


class SemanticCache:
    """
    Async-safe semantic cache with embedding-based similarity search.

    Thread-safe for concurrent asyncio tasks.
    Bounded memory with LRU eviction.
    """

    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        default_ttl: int = DEFAULT_TTL,
        embedding_dim: int = 64,
    ):
        self.max_entries = max_entries
        self.similarity_threshold = similarity_threshold
        self.default_ttl = default_ttl
        self.embedding_engine = EmbeddingEngine(dim=embedding_dim)

        # OrderedDict for LRU tracking
        self._store: OrderedDict[str, CachedItem] = OrderedDict()
        self._lock = asyncio.Lock()

        # Statistics
        self._hits = 0
        self._misses = 0
        self._similar_hits = 0
        self._evictions = 0

    async def get(self, key: str) -> Optional[Any]:
        """
        Get a cached value by key.
        Returns None if not found or expired.
        """
        async with self._lock:
            item = self._store.get(key)
            if item is None:
                self._misses += 1
                return None

            if item.is_expired:
                del self._store[key]
                self._misses += 1
                logger.debug(f"Cache expired: {key}")
                return None

            # Update access stats
            item.last_accessed = time.time()
            item.access_count += 1
            self._hits += 1

            # Move to end (most recently used)
            self._store.move_to_end(key)
            return item.value

    async def put(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
        text_for_embedding: str = "",
    ) -> None:
        """
        Store a value with optional semantic embedding.
        """
        async with self._lock:
            ttl = ttl or self.default_ttl
            embedding = self.embedding_engine.embed(text_for_embedding or key)

            # Estimate size
            size_bytes = sys.getsizeof(value) if value else 0

            item = CachedItem(
                key=key,
                value=value,
                embedding=embedding,
                created_at=time.time(),
                last_accessed=time.time(),
                access_count=0,
                ttl=ttl,
                size_bytes=size_bytes,
            )

            # Evict if necessary
            if len(self._store) >= self.max_entries and key not in self._store:
                self._evict()

            self._store[key] = item
            self._store.move_to_end(key)
            logger.debug(f"Cache stored: {key} (TTL={ttl}s, size={size_bytes}B)")

    async def get_or_compute(
        self,
        key: str,
        compute_fn: Callable,
        ttl: Optional[int] = None,
        text_for_embedding: str = "",
    ) -> Any:
        """
        Get from cache, or compute and store the result.
        This is the primary usage pattern.
        """
        # Try exact match first (without lock for read)
        async with self._lock:
            item = self._store.get(key)
            if item and not item.is_expired:
                item.last_accessed = time.time()
                item.access_count += 1
                self._hits += 1
                self._store.move_to_end(key)
                logger.debug(f"Cache hit: {key} (access #{item.access_count})")
                return item.value

        # Cache miss — compute
        self._misses += 1
        logger.debug(f"Cache miss: {key} — computing...")

        result = await compute_fn() if asyncio.iscoroutinefunction(compute_fn) else compute_fn()

        # Store result
        await self.put(key, result, ttl=ttl, text_for_embedding=text_for_embedding or key)
        return result

    async def find_similar(self, query_text: str, threshold: Optional[float] = None) -> Optional[Any]:
        """
        Find a cached item with semantically similar key.
        Returns the value if found above threshold, else None.
        """
        threshold = threshold or self.similarity_threshold
        query_embedding = self.embedding_engine.embed(query_text)

        async with self._lock:
            best_match = None
            best_similarity = 0.0

            for key, item in self._store.items():
                if item.is_expired:
                    continue

                similarity = EmbeddingEngine.cosine_similarity(
                    query_embedding, item.embedding
                )

                if similarity > best_similarity and similarity >= threshold:
                    best_similarity = similarity
                    best_match = item

            if best_match:
                best_match.last_accessed = time.time()
                best_match.access_count += 1
                self._hits += 1
                self._similar_hits += 1
                self._store.move_to_end(best_match.key)
                logger.debug(f"Semantic cache hit: {best_match.key} (similarity={best_similarity:.3f})")
                return best_match.value

            return None

    async def delete(self, key: str) -> bool:
        """Delete a cached item."""
        async with self._lock:
            if key in self._store:
                del self._store[key]
                logger.debug(f"Cache deleted: {key}")
                return True
            return False

    async def clear(self) -> int:
        """Clear all cached items."""
        async with self._lock:
            count = len(self._store)
            self._store.clear()
            logger.info(f"Cache cleared: {count} items removed")
            return count

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        similar_hit_rate = self._similar_hits / self._hits if self._hits > 0 else 0.0

        expired_count = sum(1 for item in self._store.values() if item.is_expired)

        return {
            "total_entries": len(self._store),
            "max_entries": self.max_entries,
            "hits": self._hits,
            "misses": self._misses,
            "similar_hits": self._similar_hits,
            "evictions": self._evictions,
            "hit_rate": round(hit_rate, 4),
            "similar_hit_rate": round(similar_hit_rate, 4),
            "expired_entries": expired_count,
            "total_size_bytes": sum(item.size_bytes for item in self._store.values()),
            "oldest_entry_age": self._oldest_entry_age(),
            "newest_entry_age": self._newest_entry_age(),
        }

    def _evict(self) -> None:
        """Evict least-recently-used entries."""
        evict_count = max(1, int(self.max_entries * DEFAULT_EVICTION_RATIO))
        evicted = 0

        # Remove oldest items (beginning of OrderedDict)
        while evicted < evict_count and self._store:
            oldest_key = next(iter(self._store))
            del self._store[oldest_key]
            evicted += 1

        self._evictions += evicted
        logger.debug(f"Cache eviction: {evicted} items removed")

    def _oldest_entry_age(self) -> float:
        if not self._store:
            return 0.0
        oldest = next(iter(self._store.values()))
        return round(time.time() - oldest.created_at, 1)

    def _newest_entry_age(self) -> float:
        if not self._store:
            return 0.0
        newest = next(reversed(self._store.values()))
        return round(time.time() - newest.created_at, 1)


# ── Decorator ──────────────────────────────────────────────────────────

def semantic_cache(
    ttl: int = DEFAULT_TTL,
    key_prefix: str = "",
    text_for_embedding_fn: Optional[Callable] = None,
):
    """
    Decorator for async functions to enable semantic caching.

    Usage:
        @semantic_cache(ttl=3600, key_prefix="scan")
        async def scan_pair(base, quote):
            ...

    Args:
        ttl: Cache TTL in seconds
        key_prefix: Prefix for cache keys
        text_for_embedding_fn: Optional function to generate embedding text from args
    """
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            # Build cache key
            key_parts = [key_prefix, fn.__name__]
            key_parts.extend(str(a) for a in args)
            key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            cache_key = ":".join(key_parts)

            # Build embedding text
            if text_for_embedding_fn:
                embed_text = text_for_embedding_fn(*args, **kwargs)
            else:
                embed_text = cache_key

            # Get or compute
            cache = get_semantic_cache()

            async def _compute():
                return await fn(*args, **kwargs)

            return await cache.get_or_compute(
                cache_key,
                _compute,
                ttl=ttl,
                text_for_embedding=embed_text,
            )
        return wrapper
    return decorator


# ── Global Instance ────────────────────────────────────────────────────

_cache: Optional[SemanticCache] = None


def get_semantic_cache() -> SemanticCache:
    """Get or create the global semantic cache instance."""
    global _cache
    if _cache is None:
        _cache = SemanticCache()
    return _cache


def get_cache_stats() -> Dict[str, Any]:
    """Get global cache statistics."""
    return get_semantic_cache().get_stats()


def clear_cache() -> int:
    """Clear the global cache. Returns number of items cleared."""
    cache = get_semantic_cache()
    # Always use async-compatible approach
    try:
        loop = asyncio.get_running_loop()
        # We're inside a running loop — schedule and wait
        import concurrent.futures
        future = asyncio.run_coroutine_threadsafe(cache.clear(), loop)
        return future.result(timeout=5)
    except RuntimeError:
        # No running loop — safe to create one
        return asyncio.run(cache.clear())


async def clear_cache_async() -> int:
    """Async-safe cache clear. Use this from async code."""
    return await get_semantic_cache().clear()


def reset_cache():
    """Reset the global cache instance (useful for testing)."""
    global _cache
    _cache = None
