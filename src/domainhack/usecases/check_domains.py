from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass, field

from domainhack.domain.entities import Availability, DomainCheckResult, DomainHack
from domainhack.ports.progress import NullProgressReporter, ProgressReporter
from domainhack.ports.registrar import RegistrarClient
from domainhack.ports.result_cache import ResultCache
from domainhack.ports.result_writer import ResultWriter
from domainhack.usecases.lanes import LaneOutcome, LaneScheduler

LaneKey = Callable[[DomainHack], Hashable]
"""Maps a domain to its lane: domains with equal keys are never checked concurrently.

In production the key is the registry host serving the domain's TLD.
"""

DEFAULT_PARALLEL = 4
# Domains waiting per lane. With a lazy source (range mode) at most
# lanes x LANE_CAPACITY domains (plus their results) are held in memory.
LANE_CAPACITY = 16
# After Ctrl-C, how long in-flight checks may take to finish (and be written).
SHUTDOWN_GRACE_SECONDS = 1.0


def _one_lane(domain: DomainHack) -> Hashable:
    return None


@dataclass(frozen=True)
class TldTally:
    """How many domains under one TLD (suffix) were checked, and how many errored."""

    suffix: str
    checked: int = 0
    errors: int = 0

    @property
    def unreachable(self) -> bool:
        """Every check errored: the registry did not answer (timeouts, open circuit...)."""
        return self.checked > 0 and self.errors == self.checked


@dataclass(frozen=True)
class CheckSummary:
    """Outcome of a run: how many domains were checked and how they came out.

    ``tlds`` breaks the checks down per TLD, in first-seen order.
    """

    available: int = 0
    taken: int = 0
    errors: int = 0
    interrupted: bool = False
    dropping: int = 0  # TAKEN names in redemption or pending delete (included in ``taken``)
    # A breakdown of the counts above, so not part of equality.
    tlds: tuple[TldTally, ...] = field(default=(), compare=False)

    @property
    def checked(self) -> int:
        return self.available + self.taken + self.errors

    @property
    def tlds_with_errors(self) -> tuple[TldTally, ...]:
        """TLDs with at least one ERROR, unreachable ones first (then first-seen order)."""
        failed = [t for t in self.tlds if t.errors]
        return tuple(sorted(failed, key=lambda t: not t.unreachable))


@dataclass
class _Tally:
    counts: dict[Availability, int] = field(default_factory=lambda: dict.fromkeys(Availability, 0))
    dropping: int = 0
    # suffix -> [checked, errors], in first-seen order
    by_tld: dict[str, list[int]] = field(default_factory=dict)

    def add(self, result: DomainCheckResult) -> None:
        self.counts[result.availability] += 1
        self.dropping += result.is_dropping
        tld = self.by_tld.setdefault(result.domain.tld.suffix, [0, 0])
        tld[0] += 1
        tld[1] += result.availability is Availability.ERROR

    def summary(self, interrupted: bool) -> CheckSummary:
        return CheckSummary(
            available=self.counts[Availability.AVAILABLE],
            taken=self.counts[Availability.TAKEN],
            errors=self.counts[Availability.ERROR],
            interrupted=interrupted,
            dropping=self.dropping,
            tlds=tuple(TldTally(s, c, e) for s, (c, e) in self.by_tld.items()),
        )


class CheckDomainsUseCase:
    """Checks availability of domain hacks and writes results.

    With ``parallel=1`` (the default) domains are checked one after another,
    in input order. With ``parallel=N > 1`` they are checked by up to N
    worker threads, one lane per ``lane_key`` (one registry host): domains
    in the same lane are checked one at a time and in input order, different
    lanes run concurrently. Results are then written in *completion* order,
    unless ``keep_order`` buffers them to restore input order.

    Only the thread calling ``execute`` touches the writer, the progress
    reporter and the ``cache``. Cache hits are answered on that thread
    before dispatch, so they never occupy a lane; live results are stored
    there as they come back.
    """

    def __init__(
        self,
        registrar: RegistrarClient,
        writer: ResultWriter,
        progress: ProgressReporter | None = None,
        *,
        cache: ResultCache | None = None,
        parallel: int = 1,
        lane_key: LaneKey = _one_lane,
        keep_order: bool = False,
        lane_capacity: int = LANE_CAPACITY,
        shutdown_grace: float | None = None,
    ) -> None:
        if parallel < 1:
            raise ValueError("parallel must be at least 1")
        self._registrar = registrar
        self._writer = writer
        self._progress = progress if progress is not None else NullProgressReporter()
        self._cache = cache
        self._parallel = parallel
        self._lane_key = lane_key
        self._keep_order = keep_order
        self._lane_capacity = lane_capacity
        self._shutdown_grace = (
            shutdown_grace if shutdown_grace is not None else SHUTDOWN_GRACE_SECONDS
        )

    def execute(self, domains: Iterable[DomainHack], total: int | None = None) -> CheckSummary:
        """Check every domain and return a summary.

        A ``KeyboardInterrupt`` stops the run cleanly: the writers are flushed
        (so partial output files are kept) and the summary has
        ``interrupted=True``. In parallel mode checks already in flight get
        ``shutdown_grace`` seconds to finish (and be written); the rest are
        abandoned. Any other exception propagates after the flush. In
        parallel mode an exception raised by a single check is reported as
        an ERROR result for that domain instead.
        """
        tally = _Tally()
        interrupted = False
        try:
            self._progress.start(total)
            try:
                if self._parallel == 1:
                    self._run_sequential(domains, tally)
                else:
                    _ParallelRun(self, tally).run(domains)
            except KeyboardInterrupt:
                interrupted = True
        finally:
            # Close the bar first so anything printed afterwards appears below it.
            try:
                self._progress.close()
            finally:
                self._writer.flush()
        return tally.summary(interrupted)

    def _emit(self, result: DomainCheckResult, tally: _Tally) -> None:
        self._writer.write_result(result)
        tally.add(result)
        self._progress.advance(result)

    def _lookup(self, domain: DomainHack) -> DomainCheckResult | None:
        return self._cache.lookup(domain) if self._cache is not None else None

    def _store(self, result: DomainCheckResult) -> None:
        if self._cache is not None:
            self._cache.store(result)

    def _run_sequential(self, domains: Iterable[DomainHack], tally: _Tally) -> None:
        for domain in domains:
            result = self._lookup(domain)
            if result is None:
                result = self._registrar.check_availability(domain)
                self._store(result)
            self._emit(result, tally)


class _ParallelRun:
    """One parallel ``execute``: feeds the lanes and consumes their results.

    Runs entirely on the calling thread; the workers live in ``LaneScheduler``.
    """

    def __init__(self, use_case: CheckDomainsUseCase, tally: _Tally) -> None:
        self._uc = use_case
        self._tally = tally
        self._scheduler = LaneScheduler(
            use_case._registrar.check_availability,
            workers=use_case._parallel,
            lane_capacity=use_case._lane_capacity,
        )
        self._outstanding = 0  # submitted, outcome not consumed yet
        # keep_order: results waiting for an earlier one, by input index.
        self._held: dict[int, DomainCheckResult] = {}
        self._next_index = 0
        # Max results held for keep_order before feeding pauses (memory bound).
        self._max_held = max(use_case._lane_capacity * use_case._parallel, 1) * 4

    def run(self, domains: Iterable[DomainHack]) -> None:
        try:
            for index, domain in enumerate(domains):
                self._drain(block=False)
                cached = self._uc._lookup(domain)
                if cached is not None:
                    self._deliver(index, cached)
                else:
                    self._submit(index, domain)
                # keep_order: hold at most ~max_held results behind a slow check.
                while len(self._held) > self._max_held:
                    self._drain(block=True)
            self._scheduler.close()
            while self._outstanding:
                self._drain(block=True)
        except KeyboardInterrupt:
            # Stop dispatching; keep whatever finishes within the grace period.
            self._scheduler.stop(self._uc._shutdown_grace)
            self._drain_finished()
            self._release_held()
            raise
        finally:
            self._scheduler.stop()

    def _submit(self, index: int, domain: DomainHack) -> None:
        lane = self._uc._lane_key(domain)
        while not self._scheduler.try_submit(lane, index, domain):
            # That lane is full: handle a result (from any lane), then retry.
            # The full lane is busy or next in line, so a result always comes.
            self._drain(block=True)
        self._outstanding += 1

    def _drain(self, *, block: bool) -> None:
        """Handle one outcome when ``block``; otherwise every outcome already available."""
        if block:
            outcome = self._scheduler.next_outcome()
            assert outcome is not None
            self._handle(outcome)
            return
        while self._outstanding:
            outcome = self._scheduler.next_outcome(timeout=0)
            if outcome is None:
                return
            self._handle(outcome)

    def _drain_finished(self) -> None:
        """After an interrupt: write results that are ready, ignore failures."""
        while self._outstanding:
            outcome = self._scheduler.next_outcome(timeout=0)
            if outcome is None:
                return
            self._outstanding -= 1
            if outcome.result is not None:
                self._uc._store(outcome.result)
                self._deliver(outcome.index, outcome.result)

    def _handle(self, outcome: LaneOutcome) -> None:
        self._outstanding -= 1
        if outcome.fatal is not None:
            raise outcome.fatal
        assert outcome.result is not None
        self._uc._store(outcome.result)
        self._deliver(outcome.index, outcome.result)

    def _deliver(self, index: int, result: DomainCheckResult) -> None:
        if not self._uc._keep_order:
            self._uc._emit(result, self._tally)
            return
        self._held[index] = result
        while self._next_index in self._held:
            self._uc._emit(self._held.pop(self._next_index), self._tally)
            self._next_index += 1

    def _release_held(self) -> None:
        """Write results still held for ordering (after a gap left by an interrupt)."""
        for index in sorted(self._held):
            self._uc._emit(self._held.pop(index), self._tally)
