"""Per-host request spacing shared by registrar adapters."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class HostThrottle:
    """Keeps at least ``delay`` seconds between requests to the same host.

    Slots are reserved under a lock and the sleep happens outside it, so
    different hosts never wait on each other. Several client instances that
    talk to the same host (e.g. .io/.sh/.ac/.me on Identity Digital) share one
    throttle, so their requests are spaced as if they were a single client.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._next_slot: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str, delay: float) -> None:
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_slot.get(host, now))
            self._next_slot[host] = slot + max(delay, 0.0)
        wait_for = slot - now
        if wait_for > 0:
            self._sleep(wait_for)


DEFAULT_THROTTLE = HostThrottle()
