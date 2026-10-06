"""Per-host circuit breaker shared by registrar adapters."""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from tqdm import tqdm

DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_COOLDOWN_SECONDS = 60.0


def _warn_on_stderr(message: str) -> None:
    # tqdm.write clears and redraws an active progress bar around the message.
    tqdm.write(message, file=sys.stderr)


@dataclass
class _HostState:
    failures: int = 0
    retry_at: float | None = None  # set while the circuit is open


class HostCircuitBreaker:
    """Stops querying a host after ``threshold`` consecutive failures.

    Callers ask ``allow(host)`` before each request and report the outcome
    with ``record_success`` or ``record_failure``. A failure is a sign that
    the host is unresponsive or refusing us: a timeout, a connection error or
    a rate-limit answer. Any real answer counts as a success and closes the
    circuit.

    Once open, ``allow`` returns False until ``cooldown`` seconds have
    passed. Then a single trial request is let through (half-open); if it
    fails, the circuit stays open for another cooldown. ``cooldown=None``
    keeps an open circuit open for the life of the breaker.

    ``on_open`` is called once each time a circuit goes from closed to open
    (by default it prints a warning on stderr). Thread-safe; like
    ``HostThrottle``, one instance can be shared by several clients that talk
    to the same host.
    """

    def __init__(
        self,
        threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown: float | None = DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        on_open: Callable[[str], None] = _warn_on_stderr,
    ) -> None:
        if threshold < 1:
            raise ValueError("threshold must be at least 1")
        self._threshold = threshold
        self._cooldown = cooldown
        self._clock = clock
        self._on_open = on_open
        self._hosts: dict[str, _HostState] = {}
        self._lock = threading.Lock()

    def allow(self, host: str) -> bool:
        """True if a request to ``host`` may go out now."""
        with self._lock:
            state = self._hosts.get(host)
            if state is None or state.retry_at is None:
                return True
            if self._cooldown is None:
                return False
            now = self._clock()
            if now < state.retry_at:
                return False
            # Half-open: let this one request through and hold back the
            # others until it reports back or another cooldown passes.
            state.retry_at = now + self._cooldown
            return True

    def is_open(self, host: str) -> bool:
        with self._lock:
            state = self._hosts.get(host)
            return state is not None and state.retry_at is not None

    def record_success(self, host: str) -> None:
        with self._lock:
            self._hosts.pop(host, None)

    def record_failure(self, host: str) -> None:
        with self._lock:
            state = self._hosts.setdefault(host, _HostState())
            state.failures += 1
            if state.retry_at is not None or state.failures < self._threshold:
                return
            cooldown = self._cooldown
            state.retry_at = self._clock() + (cooldown if cooldown is not None else 0.0)
            failures = state.failures
        retry = f"retrying in {cooldown:g}s" if cooldown is not None else "not retrying"
        self._on_open(
            f"warning: {host} unresponsive after {failures} consecutive failures; "
            f"skipping its remaining domains (circuit open, {retry})"
        )

    def skip_message(self, host: str) -> str:
        return f"skipped: {host} unresponsive (circuit open)"
