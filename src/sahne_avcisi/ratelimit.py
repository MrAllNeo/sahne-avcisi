"""Per-client request budget for the public search endpoint.

Scene search is the one unauthenticated endpoint that costs real work: it
decodes an uploaded image, scores it against the whole index and may spend
third-party trace.moe quota. Without a budget, a single caller can drain all
three.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window_seconds = max(0.001, window_seconds)
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Record a request and report whether it stays inside the budget."""
        if not self.enabled:
            return True
        moment = time.monotonic() if now is None else now
        cutoff = moment - self.window_seconds
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                hits = self._hits[key] = deque()
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(moment)
            self._prune(cutoff)
            return True

    def retry_after(self, key: str, *, now: float | None = None) -> int:
        """Whole seconds until the oldest recorded hit leaves the window."""
        if not self.enabled:
            return 0
        moment = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return 0
            remaining = hits[0] + self.window_seconds - moment
        return max(1, int(remaining + 0.999))

    def _prune(self, cutoff: float) -> None:
        """Drop callers that went quiet so the table cannot grow forever."""
        if len(self._hits) < 1024:
            return
        for key in [key for key, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]


def from_environment(environ: dict[str, str]) -> RateLimiter:
    def number(name: str, default: float) -> float:
        try:
            return float(environ.get(name, default))
        except (TypeError, ValueError):
            return default

    return RateLimiter(
        limit=int(number("SAHNE_RATE_LIMIT", 60)),
        window_seconds=number("SAHNE_RATE_WINDOW", 60),
    )
