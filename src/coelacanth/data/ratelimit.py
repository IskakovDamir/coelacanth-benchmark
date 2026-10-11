"""Minimum-interval rate limiter shared by the data clients."""

from __future__ import annotations

import threading
import time
from typing import Callable


class RateLimiter:
    """Allow at most `rate` calls per second, evenly spaced; `pause` delays every later call (Retry-After)."""

    def __init__(self, rate: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.interval = 1.0 / rate
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            wait = self._next - now
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
            self._next = max(now, self._next) + self.interval

    def pause(self, seconds: float) -> None:
        with self._lock:
            self._next = max(self._next, self._clock() + seconds)
