# Tiny TTL cache for:
# tool results
# embedding/query caching
# avoiding repeated DB hits


# src/llm/cache.py
from __future__ import annotations
import time
from typing import Any, Dict, Optional, Tuple


class TTLCache:
    def __init__(self, default_ttl_seconds: int = 30, max_items: int = 5000):
        self.default_ttl = default_ttl_seconds
        self.max_items = max_items
        self._store: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        item = self._store.get(key)
        if not item:
            return None
        expires_at, value = item
        if expires_at < time.time():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        if len(self._store) >= self.max_items:
            # cheap eviction: drop one arbitrary key
            self._store.pop(next(iter(self._store)), None)
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        self._store[key] = (time.time() + ttl, value)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


_cache = TTLCache(default_ttl_seconds=30, max_items=5000)


def cache() -> TTLCache:
    return _cache


def make_key(*parts: Any) -> str:
    return "|".join("" if p is None else str(p) for p in parts)
