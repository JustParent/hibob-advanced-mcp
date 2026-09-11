"""A small in-process cache for HiBob named lists.

HiBob allows fifty named-list requests a minute and a position form needs a
dozen lists, while the form tool's ask-then-call-again loop fetches the same
lists twice in a row. Caching them briefly keeps that loop within the limit.
Only successful fetches are cached, so a rate-limit error is retried next time.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

DEFAULT_TTL_SECONDS = 300.0

CacheKey = tuple[str, bool]


class NamedListCache:
    """Time-limited cache of named-list items, keyed by list ID and variant."""

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[CacheKey, tuple[float, Any]] = {}

    def get(self, key: CacheKey) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return None
        return value

    def set(self, key: CacheKey, value: Any) -> None:
        self._entries[key] = (self._clock() + self._ttl, value)

    def clear(self) -> None:
        self._entries.clear()
