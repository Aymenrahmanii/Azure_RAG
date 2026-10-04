"""Per-caller sliding-window rate limit. In memory, so it is per replica: with N replicas a caller
can make up to N x limit requests. Enough to stop a single client from burning the LLM budget; a
shared limiter (API Management, Redis) is the production answer."""

import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0, clock=time.monotonic):
        self.limit, self.window, self._clock = limit, window_seconds, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, float]:
        """(allowed, retry_after_seconds). Records the hit only when it is allowed."""
        if self.limit <= 0:
            return True, 0.0
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False, self.window - (now - hits[0])
            hits.append(now)
            if len(self._hits) > 10_000:  # forget callers with no recent hits
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window]:
                    del self._hits[k]
            return True, 0.0
