"""Per-host request spacing shared by registrar adapters.

The spacing adapts to how each host behaves (RFC 7480 §5.5: a client that is
told to slow down SHOULD decrease its query rate). It is a simple
multiplicative-increase / gradual-decrease scheme:

* ``slow_down(host)`` (a 429, a 503, a timeout or WHOIS rate-limit text)
  doubles that host's interval, up to ``max_interval``.
* ``record_success(host)`` shrinks it by ``recovery`` (x0.9) per success,
  back down to the configured base (the ``delay`` given to ``wait``).
* ``defer(host, seconds)`` holds the next request to ``host`` back by at
  least ``seconds`` (``Retry-After``, retry backoff).

Every spacing gets +/-``jitter`` (20 %) so requests do not arrive on an
exact, easily fingerprinted beat. Clock, sleep and random source are
injectable so tests run instantly and deterministically.
"""

from __future__ import annotations

import random as _random
import threading
import time
from collections.abc import Callable

DEFAULT_JITTER = 0.2
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_RECOVERY = 0.9
DEFAULT_MAX_INTERVAL = 60.0
# Slowing down a host paced at 0 s (``--delay 0``) still has to space it out.
MIN_PENALTY_INTERVAL = 1.0

# Full-jitter retry backoff (AWS "Exponential Backoff and Jitter"):
# sleep = random(0, min(cap, base * 2**attempt)).
DEFAULT_BACKOFF_BASE = 2.0
DEFAULT_BACKOFF_CAP = 30.0


def full_jitter_backoff(
    attempt: int,
    random: Callable[[], float],
    base: float = DEFAULT_BACKOFF_BASE,
    cap: float = DEFAULT_BACKOFF_CAP,
) -> float:
    """Seconds to wait before retry number ``attempt`` (0-based), full jitter."""
    return random() * min(cap, base * 2.0**attempt)


class HostThrottle:
    """Keeps an adaptive, jittered interval between requests to the same host.

    Slots are reserved under a lock and the sleep happens outside it, so
    different hosts never wait on each other. Several client instances that
    talk to the same host (e.g. .io/.sh/.ac/.me on Identity Digital) share one
    throttle, so their requests are spaced, slowed down and recovered as if
    they were a single client.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        *,
        random: Callable[[], float] = _random.random,
        jitter: float = DEFAULT_JITTER,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        recovery: float = DEFAULT_RECOVERY,
        max_interval: float = DEFAULT_MAX_INTERVAL,
    ) -> None:
        if not 0 <= jitter < 1:
            raise ValueError("jitter must be in [0, 1)")
        if backoff_factor < 1:
            raise ValueError("backoff_factor must be >= 1")
        if not 0 < recovery <= 1:
            raise ValueError("recovery must be in (0, 1]")
        self._clock = clock
        self._sleep = sleep
        self._random = random
        self._jitter = jitter
        self._backoff_factor = backoff_factor
        self._recovery = recovery
        self._max_interval = max_interval
        self._next_slot: dict[str, float] = {}
        self._base: dict[str, float] = {}
        self._penalty: dict[str, float] = {}  # current interval while slowed down
        self._lock = threading.Lock()

    def interval(self, host: str, delay: float | None = None) -> float:
        """The current (un-jittered) interval for ``host``.

        ``delay`` is the base to compare against; by default the base last
        passed to ``wait`` for that host (0 if it was never seen).
        """
        with self._lock:
            return self._interval(host, delay)

    def _interval(self, host: str, delay: float | None = None) -> float:
        base = self._base.get(host, 0.0) if delay is None else max(delay, 0.0)
        return max(base, self._penalty.get(host, 0.0))

    def wait(self, host: str, delay: float) -> None:
        """Block until a request to ``host`` may go out; ``delay`` is its base interval."""
        with self._lock:
            self._base[host] = max(delay, 0.0)
            now = self._clock()
            slot = max(now, self._next_slot.get(host, now))
            spacing = self._interval(host)
            if spacing > 0 and self._jitter:
                spacing *= 1 + self._jitter * (2 * self._random() - 1)
            self._next_slot[host] = slot + spacing
        wait_for = slot - now
        if wait_for > 0:
            self._sleep(wait_for)

    def slow_down(self, host: str) -> float:
        """Multiply ``host``'s interval by ``backoff_factor`` (capped); return the new one.

        Takes effect at once: the next request waits at least the new
        interval from now. Waits combine with ``defer`` by taking the later
        slot, never by adding up.
        """
        with self._lock:
            current = self._interval(host)
            new = min(self._max_interval, max(current * self._backoff_factor, MIN_PENALTY_INTERVAL))
            self._penalty[host] = new
            self._push(host, self._clock() + new)
            return new

    def record_success(self, host: str) -> None:
        """Recover gradually: shrink a slowed-down interval by ``recovery``."""
        with self._lock:
            penalty = self._penalty.get(host)
            if penalty is None:
                return
            recovered = penalty * self._recovery
            if recovered <= self._base.get(host, 0.0):
                del self._penalty[host]
            else:
                self._penalty[host] = recovered

    def defer(self, host: str, seconds: float) -> None:
        """Hold the next request to ``host`` back until ``seconds`` from now (no jitter)."""
        with self._lock:
            self._push(host, self._clock() + max(seconds, 0.0))

    def _push(self, host: str, target: float) -> None:
        self._next_slot[host] = max(self._next_slot.get(host, target), target)


DEFAULT_THROTTLE = HostThrottle()
