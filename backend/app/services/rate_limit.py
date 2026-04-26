from __future__ import annotations

import asyncio
import time


class AsyncTokenBucket:
    """Simple async token bucket limiter for global API RPS."""

    def __init__(self, rate_per_sec: int, capacity: int | None = None) -> None:
        self.rate = float(rate_per_sec)
        self.capacity = float(capacity or rate_per_sec)
        self.tokens = self.capacity
        self.updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self.updated_at
                self.updated_at = now
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait_for = (1 - self.tokens) / self.rate
            await asyncio.sleep(max(wait_for, 0.01))
