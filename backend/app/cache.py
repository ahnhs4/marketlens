"""Cache layer: Redis when REDIS_URL is set, otherwise an in-process TTL cache.

`cached()` adds single-flight de-duplication: concurrent callers asking for the
same key while it is being computed wait for the first computation instead of
hitting the upstream API again. Values carry the time they were produced so the
UI can display "updated at".
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, TypeVar

from .config import get_settings

log = logging.getLogger(__name__)
T = TypeVar("T")


class MemoryCache:
    def __init__(self, max_items: int = 5000):
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_items

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            expires, value = item
            if expires < time.time():
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._data[key] = (time.time() + ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def delete_prefix(self, prefix: str) -> None:
        with self._lock:
            for k in [k for k in self._data if k.startswith(prefix)]:
                self._data.pop(k, None)

    @property
    def backend(self) -> str:
        return "memory"


class RedisCache:
    """JSON-serialising Redis wrapper. Non-JSON values (DataFrames) stay in a local L1."""

    def __init__(self, url: str):
        import redis  # local import: optional dependency at runtime

        self._r = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
        self._r.ping()
        self._l1 = MemoryCache(max_items=2000)

    def get(self, key: str) -> Any | None:
        v = self._l1.get(key)
        if v is not None:
            return v
        try:
            raw = self._r.get(f"ml:{key}")
        except Exception as e:  # pragma: no cover - network
            log.warning("redis get failed: %s", e)
            return None
        return json.loads(raw) if raw else None

    def set(self, key: str, value: Any, ttl: float) -> None:
        try:
            payload = json.dumps(value, default=str)
        except (TypeError, ValueError):
            self._l1.set(key, value, ttl)
            return
        self._l1.set(key, value, min(ttl, 30))
        try:
            self._r.setex(f"ml:{key}", int(max(ttl, 1)), payload)
        except Exception as e:  # pragma: no cover
            log.warning("redis set failed: %s", e)

    def delete(self, key: str) -> None:
        self._l1.delete(key)
        try:
            self._r.delete(f"ml:{key}")
        except Exception:  # pragma: no cover
            pass

    def delete_prefix(self, prefix: str) -> None:
        self._l1.delete_prefix(prefix)
        try:
            for k in self._r.scan_iter(f"ml:{prefix}*"):
                self._r.delete(k)
        except Exception:  # pragma: no cover
            pass

    @property
    def backend(self) -> str:
        return "redis"


def _build_cache():
    url = get_settings().redis_url
    if url:
        try:
            c = RedisCache(url)
            log.info("cache: redis")
            return c
        except Exception as e:
            log.warning("Redis unavailable (%s) - falling back to in-memory cache", e)
    return MemoryCache()


cache = _build_cache()

_inflight: dict[str, threading.Lock] = {}
_inflight_guard = threading.Lock()


def cached(key: str, ttl: float, fn: Callable[[], T], *, cache_none: bool = False) -> T:
    """Return cache[key] or compute it once (single-flight) and store it for `ttl` seconds."""
    hit = cache.get(key)
    if hit is not None:
        return hit
    with _inflight_guard:
        lock = _inflight.setdefault(key, threading.Lock())
    with lock:
        hit = cache.get(key)
        if hit is not None:
            return hit
        value = fn()
        if value is not None or cache_none:
            cache.set(key, value, ttl)
        return value
