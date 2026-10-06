"""Worker lanes: check domains in parallel, one at a time per registry host.

Every domain is assigned to a *lane* (in production, the registry host that
answers for its TLD; TLDs that share a host share a lane). A small pool of
worker threads serves the lanes:

* a lane is served by at most one worker at a time, so a host never has more
  than one request in flight from this process;
* at most ``workers`` lanes are served at once (``--parallel``);
* a worker takes one domain from a lane, checks it, and hands the lane back,
  so with more lanes than workers every lane gets its turn;
* within a lane domains are checked in the order they were submitted.

Each lane holds at most ``lane_capacity`` waiting domains. ``try_submit``
returns False instead of blocking when a lane is full, so the caller (the
main thread) can consume results meanwhile and try again: with a lazy source
the memory in use is bounded by ``lanes x lane_capacity`` waiting domains
plus their results, whatever the size of the source.

Workers never touch writers, progress bars or caches: every outcome goes to
a queue that the thread running the use case consumes (``next_outcome``).
Worker threads are daemons, so an abandoned in-flight request never keeps
the process alive.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Hashable
from dataclasses import dataclass

from domainhack.domain.entities import Availability, DomainCheckResult, DomainHack

# How long ``next_outcome`` blocks between checks for Ctrl-C. Waiting on a
# lock with no timeout cannot be interrupted on every platform (Windows).
_POLL_SECONDS = 0.25

Check = Callable[[DomainHack], DomainCheckResult]


@dataclass(frozen=True)
class LaneOutcome:
    """What a worker produced for the domain submitted as number ``index``.

    ``fatal`` is set (and ``result`` is None) when the check raised a
    ``BaseException`` that is not an ``Exception``, e.g. ``KeyboardInterrupt``:
    the caller re-raises it, and the scheduler stops serving lanes.
    """

    index: int
    result: DomainCheckResult | None = None
    fatal: BaseException | None = None


def unexpected_error(domain: DomainHack, exc: Exception) -> DomainCheckResult:
    """The ERROR result reported for a check that raised ``exc``."""
    detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return DomainCheckResult(
        domain=domain,
        availability=Availability.ERROR,
        error_message=f"unexpected error: {detail}",
    )


class LaneScheduler:
    """Runs ``check`` on submitted domains, one lane at a time per worker."""

    def __init__(self, check: Check, *, workers: int, lane_capacity: int) -> None:
        if workers < 1:
            raise ValueError("workers must be at least 1")
        if lane_capacity < 1:
            raise ValueError("lane_capacity must be at least 1")
        self._check = check
        self._max_workers = workers
        self._capacity = lane_capacity
        self._cond = threading.Condition()
        self._waiting: dict[Hashable, deque[tuple[int, DomainHack]]] = {}
        # Invariant: a lane is in ``_ready`` iff it has waiting domains and is not busy.
        self._ready: deque[Hashable] = deque()
        self._busy: set[Hashable] = set()
        self._closed = False  # no more submissions
        self._stopped = False  # stop serving lanes (Ctrl-C, fatal error)
        self._threads: list[threading.Thread] = []
        self._outcomes: queue.SimpleQueue[LaneOutcome] = queue.SimpleQueue()

    @property
    def worker_count(self) -> int:
        """Worker threads started so far (one per new lane, up to ``workers``)."""
        return len(self._threads)

    def try_submit(self, lane: Hashable, index: int, domain: DomainHack) -> bool:
        """Queue ``domain`` on ``lane``; False (nothing queued) if that lane is full."""
        with self._cond:
            if self._closed or self._stopped:
                raise RuntimeError("scheduler no longer accepts domains")
            waiting = self._waiting.setdefault(lane, deque())
            if len(waiting) >= self._capacity:
                return False
            waiting.append((index, domain))
            if len(waiting) == 1 and lane not in self._busy:
                self._ready.append(lane)
                self._cond.notify()
            start_worker = len(self._threads) < min(self._max_workers, len(self._waiting))
        if start_worker:
            self._start_worker()
        return True

    def next_outcome(self, timeout: float | None = None) -> LaneOutcome | None:
        """The next finished check, waiting up to ``timeout`` seconds (None: forever).

        Returns None on timeout. Waits in short slices so Ctrl-C is noticed.
        """
        if timeout is not None:
            try:
                return self._outcomes.get(timeout=timeout)
            except queue.Empty:
                return None
        while True:
            try:
                return self._outcomes.get(timeout=_POLL_SECONDS)
            except queue.Empty:
                continue

    def close(self) -> None:
        """No more submissions: workers exit once every lane is drained."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def stop(self, grace: float = 0.0) -> None:
        """Stop serving lanes and wait up to ``grace`` seconds for in-flight checks.

        Waiting domains are dropped. Checks still running after ``grace`` are
        abandoned (their daemon threads finish or die with the process).
        """
        with self._cond:
            self._stopped = True
            self._cond.notify_all()
        if grace <= 0:
            return
        deadline = time.monotonic() + grace
        for thread in self._threads:
            thread.join(max(deadline - time.monotonic(), 0.0))

    def _start_worker(self) -> None:
        thread = threading.Thread(
            target=self._work, name=f"domainhack-lane-{len(self._threads) + 1}", daemon=True
        )
        self._threads.append(thread)
        thread.start()

    def _take(self) -> tuple[Hashable, int, DomainHack] | None:
        with self._cond:
            while not self._ready and not self._stopped and not self._closed:
                self._cond.wait()
            if self._stopped or not self._ready:
                return None
            lane = self._ready.popleft()
            index, domain = self._waiting[lane].popleft()
            self._busy.add(lane)
            return lane, index, domain

    def _release(self, lane: Hashable, *, stop: bool) -> None:
        with self._cond:
            self._busy.discard(lane)
            if stop:
                self._stopped = True
                self._cond.notify_all()
            elif self._waiting[lane]:
                self._ready.append(lane)
                self._cond.notify()

    def _work(self) -> None:
        while True:
            taken = self._take()
            if taken is None:
                return
            lane, index, domain = taken
            fatal: BaseException | None = None
            try:
                outcome = LaneOutcome(index, self._check(domain))
            except Exception as exc:
                outcome = LaneOutcome(index, unexpected_error(domain, exc))
            except BaseException as exc:  # KeyboardInterrupt, SystemExit: the caller decides
                fatal = exc
                outcome = LaneOutcome(index, fatal=exc)
            self._outcomes.put(outcome)
            self._release(lane, stop=fatal is not None)
            if fatal is not None:
                return
