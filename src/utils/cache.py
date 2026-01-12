# src/utils/cache.py
import os
import time
import json
import hashlib
from typing import Any, Optional


def _now() -> float:
    return time.time()


def make_cache_key(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class MemoryTTLCache:
    def __init__(self, default_ttl_seconds: int = 60):
        self.default_ttl = default_ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        item = self._store.get(key)
        if not item:
            return None
        expires_at, value = item
        if expires_at < _now():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        self._store[key] = (_now() + ttl, value)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


_CACHE = None


def get_cache():
    """
    Simple: memory cache by default.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    ttl = int(os.getenv("CACHE_TTL_SECONDS", "60"))
    _CACHE = MemoryTTLCache(default_ttl_seconds=ttl)
    return _CACHE
