"""Async token-bucket rate limiter + exponential backoff with jitter.

Used by every scraper/LLM call so a 429 is a pause, not a crash.
"""
from __future__ import annotations

import asyncio
import random
import time


class RateLimiter:
    def __init__(self, rate_per_second: float, burst: int = 5):
        self.rate = rate_per_second
        self.capacity = burst
        self.tokens = float(burst)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
            self.updated = now
            if self.tokens < 1:
                wait = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait)
                self.tokens = 0.0
                self.updated = time.monotonic()
            else:
                self.tokens -= 1


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    """Exponential backoff with full jitter: delay in seconds."""
    exp = min(cap, base * (2 ** attempt))
    return random.uniform(0, exp)


async def retry_with_backoff(coro_fn, max_attempts: int = 5, base: float = 1.0):
    """Run coro_fn(attempt) — retrying on 429/5xx-style exceptions.

    coro_fn must raise an exception whose message/status signals retry,
    or return a sentinel. Kept generic; LLM orchestrator wraps HTTP 429.
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return await coro_fn(attempt)
        except Exception as exc:  # noqa: BLE001 — retry policy is the point
            last_exc = exc
            if attempt == max_attempts - 1:
                raise
            await asyncio.sleep(backoff_delay(attempt, base=base))
    raise last_exc  # pragma: no cover
