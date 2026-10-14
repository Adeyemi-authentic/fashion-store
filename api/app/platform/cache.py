"""A small in-process TTL cache for catalog reads (ADR-006: no Redis).

Safe to lose at any time (it's never the only copy). Cleared in this process when an admin
changes a product. With two or more API processes each has its own copy, so another
process can serve data up to TTL seconds old after an edit; ADR-006 says when that
matters (then Redis, or a Postgres LISTEN/NOTIFY broadcast to clear every copy).
"""

import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

log = logging.getLogger("app.cache")
T = TypeVar("T")


class TTLCache:
    def __init__(self, name: str, ttl_seconds: float, max_entries: int = 1000) -> None:
        self.name = name
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    async def get_or_load(self, key: str, load: Callable[[], Awaitable[T]]) -> T:
        now = time.monotonic()
        entry = self._data.get(key)
        if entry is not None and entry[0] > now:
            self._data.move_to_end(key)
            self.hits += 1
            log.info("cache hit", extra={"cache": self.name, "key": key})
            return entry[1]  # type: ignore[no-any-return]
        self.misses += 1
        log.info("cache miss", extra={"cache": self.name, "key": key})
        value = await load()
        self._data[key] = (now + self.ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:  # least recently used goes first
            self._data.popitem(last=False)
        return value

    def clear(self) -> None:
        self._data.clear()
        log.info("cache cleared", extra={"cache": self.name})

    def __len__(self) -> int:
        return len(self._data)


catalog_cache = TTLCache("catalog", ttl_seconds=60)
