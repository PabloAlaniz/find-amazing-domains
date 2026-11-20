"""Per-host circuit breaker shared by registrar adapters."""

from __future__ import annotations

import math
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from tqdm import tqdm

DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_COOLDOWN_SECONDS = 60.0
DEFAULT_MAX_TRIALS = 3


class Admission(Enum):
    """What ``HostCircuitBreaker.admit`` decided for one request."""

    CLOSED = "closed"  # the circuit is closed: a normal request
    TRIAL = "trial"  # the half-open trial: send a single attempt, no retries
    DENIED = "denied"  # the circuit is open (or the host was given up on): skip


def _warn_on_stderr(message: str) -> None:
    # tqdm.write clears and redraws an active progress bar around the message.
    tqdm.write(message, file=sys.stderr)


@dataclass
class _HostState:
    failures: int = 0
    retry_at: float | None = None  # set while the circuit is open
    trial_pending: bool = False  # a half-open trial is out and has not reported back
    failed_trials: int = 0  # consecutive failed trials since the circuit last closed
    dead: bool = False  # given up on for the life of the breaker


class HostCircuitBreaker:
    """Stops querying a host after ``threshold`` consecutive failures.

    Callers ask ``allow(host)`` before each request and report the outcome
    with ``record_success`` or ``record_failure``. A failure is a sign that
    the host is unresponsive or refusing us: a timeout, a connection error or
    a rate-limit answer. Any real answer counts as a success and closes the
    circuit.

    Once open, ``allow`` returns False until ``cooldown`` seconds have
    passed. Then a single trial request is let through (half-open; ``admit``
    returns ``Admission.TRIAL`` so the caller sends one attempt only); if it
    fails, the circuit stays open for another cooldown counted from that
    failure. After ``max_trials`` consecutive failed trials the host is given
    up on for the life of the breaker (one run): it is never allowed again.
    A success closes the circuit and resets every count. ``cooldown=None``
    keeps an open circuit open for the life of the breaker.

    ``on_open`` is called once each time a circuit goes from closed to open
    (by default it prints a warning on stderr), and once more when a host is
    given up on. Thread-safe; like
    ``HostThrottle``, one instance can be shared by several clients that talk
    to the same host.
    """

    def __init__(
        self,
        threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown: float | None = DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        on_open: Callable[[str], None] = _warn_on_stderr,
        max_trials: int = DEFAULT_MAX_TRIALS,
    ) -> None:
        if threshold < 1:
            raise ValueError("threshold must be at least 1")
        if max_trials < 1:
            raise ValueError("max_trials must be at least 1")
        self._max_trials = max_trials
        self._threshold = threshold
        self._cooldown = cooldown
        self._clock = clock
        self._on_open = on_open
        self._hosts: dict[str, _HostState] = {}
        self._lock = threading.Lock()

    def allow(self, host: str) -> bool:
        """True if a request to ``host`` may go out now."""
        return self.admit(host) is not Admission.DENIED

    def admit(self, host: str) -> Admission:
        """Whether a request to ``host`` may go out now, and if it is the half-open trial."""
        with self._lock:
            state = self._hosts.get(host)
            if state is None or state.retry_at is None:
                return Admission.CLOSED
            if state.dead or self._cooldown is None:
                return Admission.DENIED
            now = self._clock()
            if now < state.retry_at:
                return Admission.DENIED
            # Half-open: let this one request through and hold back the
            # others until it reports back or another cooldown passes.
            state.retry_at = now + self._cooldown
            state.trial_pending = True
            return Admission.TRIAL

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
            if state.retry_at is not None:
                message = self._failed_trial(host, state)
            elif state.failures < self._threshold:
                return
            else:
                cooldown = self._cooldown
                state.retry_at = self._clock() + (cooldown if cooldown is not None else 0.0)
                retry = f"retrying in {cooldown:g}s" if cooldown is not None else "not retrying"
                message = (
                    f"warning: {host} unresponsive after {state.failures} consecutive "
                    f"failures; skipping its remaining domains (circuit open, {retry})"
                )
        if message is not None:
            self._on_open(message)

    def _failed_trial(self, host: str, state: _HostState) -> str | None:
        """Handle a failure while the circuit is open; the lock is held."""
        if not state.trial_pending:
            # A request that was already in flight when the circuit opened.
            return None
        state.trial_pending = False
        state.failed_trials += 1
        if state.failed_trials < self._max_trials:
            # Count the cooldown from the failure, not from when the trial
            # started: a trial can outlast the cooldown.
            cooldown = self._cooldown if self._cooldown is not None else 0.0
            state.retry_at = self._clock() + cooldown
            return None
        state.dead = True
        state.retry_at = math.inf
        return (
            f"warning: {host} still unresponsive after {state.failed_trials} retries; "
            "skipping its remaining domains for the rest of this run"
        )

    def dead_hosts(self) -> tuple[str, ...]:
        """Hosts given up on for the life of this breaker, sorted."""
        with self._lock:
            return tuple(sorted(host for host, state in self._hosts.items() if state.dead))

    def skip_message(self, host: str) -> str:
        with self._lock:
            state = self._hosts.get(host)
            dead = state is not None and state.dead
        reason = "gave up for this run" if dead else "circuit open"
        return f"skipped: {host} unresponsive ({reason})"
