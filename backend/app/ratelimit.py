"""In-process sliding-window rate limiter for sensitive endpoints.

Suitable for a single API instance. For several instances, back this with a
shared store (e.g. Redis) or enforce limits at the reverse proxy as well.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from app.errors import AppError


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= limit:
                retry = max(1, int(window_seconds - (now - q[0])) + 1)
                raise AppError(429, "RATE_LIMITED", "Too many requests. Please wait and try again.",
                               {"retry_after_seconds": retry})
            q.append(now)
            if len(self._hits) > 50_000:  # bound memory
                for k in list(self._hits)[:10_000]:
                    if not self._hits[k]:
                        del self._hits[k]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()
