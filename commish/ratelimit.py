"""In-memory sliding-window limits: per-sender questions and total outbound messages.

Outbound volume is the main thing that gets an Apple ID flagged, so the daily cap is a
hard stop regardless of who is asking.
"""

import time
from collections import defaultdict, deque
from collections.abc import Callable


class SlidingWindow:
    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self._clock()
        events = self._events[key]
        while events and now - events[0] >= self.window_s:
            events.popleft()
        if len(events) >= self.limit:
            return False
        events.append(now)
        return True


class RateLimiter:
    def __init__(
        self,
        per_sender_per_10min: int,
        outbound_per_day: int,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._sender = SlidingWindow(per_sender_per_10min, 600, clock)
        self._outbound = SlidingWindow(outbound_per_day, 86400, clock)

    def allow_question(self, sender: str | None) -> bool:
        return self._sender.allow(sender or "unknown")

    def allow_outbound(self) -> bool:
        return self._outbound.allow("all")
