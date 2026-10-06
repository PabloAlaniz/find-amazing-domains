"""Shared test doubles and helpers."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from domainhack.adapters._circuit import HostCircuitBreaker
from domainhack.adapters._throttle import HostThrottle
from domainhack.domain.entities import (
    TLD,
    Availability,
    DnsEvidence,
    DomainCheckResult,
    DomainHack,
)
from domainhack.ports.dns_lookup import DnsLookup
from domainhack.ports.registrar import RegistrarClient
from domainhack.ports.result_cache import ResultCache
from domainhack.ports.result_writer import ResultWriter
from domainhack.ports.word_source import WordSource

TESTS_DIR = Path(__file__).resolve().parent
SAMPLES_DIR = TESTS_DIR.parent / "data" / "samples"
NETGUARD_SITE_DIR = TESTS_DIR / "netguard_site"


def hack(sld: str = "pla", tld: str = "to") -> DomainHack:
    return DomainHack.from_sld(sld, TLD(tld))


class FakeWordSource(WordSource):
    def __init__(self, words: list[str]) -> None:
        self._words = words

    def words(self) -> Iterator[str]:
        return iter(self._words)


class FakeRegistrarClient(RegistrarClient):
    """Returns a prepared result per fqdn (KeyError for anything else)."""

    def __init__(self, results: dict[str, DomainCheckResult]) -> None:
        self._results = results

    def check_availability(self, domain: DomainHack) -> DomainCheckResult:
        return self._results[domain.fqdn]


@dataclass(frozen=True)
class Answer:
    """A scripted answer with registration details (statuses, expiration)."""

    availability: Availability = Availability.TAKEN
    statuses: tuple[str, ...] = ()
    expires_at: datetime | None = None


Outcome = Availability | Answer | type[BaseException]


class ScriptedRegistrar(RegistrarClient):
    """Answers from a script keyed by fqdn or SLD, recording calls and ``close()``.

    Unscripted domains get ``default``. A script value that is an exception
    class is raised instead of answering; an ``Answer`` adds statuses and an
    expiration date. ERROR results carry ``"boom"``.
    """

    def __init__(
        self,
        script: Mapping[str, Outcome] | None = None,
        *,
        default: Outcome = Availability.TAKEN,
        raw_title: str = "live",
        fail_on_close: bool = False,
    ) -> None:
        self._script = dict(script or {})
        self._default = default
        self._raw_title = raw_title
        self._fail_on_close = fail_on_close
        self.calls: list[str] = []
        self.closed = False

    def check_availability(self, domain: DomainHack) -> DomainCheckResult:
        self.calls.append(domain.fqdn)
        outcome = self._script.get(domain.fqdn, self._script.get(domain.sld, self._default))
        if isinstance(outcome, Availability):
            outcome = Answer(outcome)
        if not isinstance(outcome, Answer):
            raise outcome()
        message = "boom" if outcome.availability is Availability.ERROR else ""
        return DomainCheckResult(
            domain=domain,
            availability=outcome.availability,
            raw_title=self._raw_title,
            error_message=message,
            statuses=outcome.statuses,
            expires_at=outcome.expires_at,
        )

    def close(self) -> None:
        self.closed = True
        if self._fail_on_close:
            raise RuntimeError(f"{self._raw_title} close failed")


class CollectingWriter(ResultWriter):
    def __init__(self) -> None:
        self.results: list[DomainCheckResult] = []
        self.flushed = False

    def write_result(self, result: DomainCheckResult) -> None:
        self.results.append(result)

    def flush(self) -> None:
        self.flushed = True


# Upper bound for any wait in the concurrency tests: a correct run never gets
# near it; a broken one fails instead of hanging the suite.
WAIT_TIMEOUT = 5.0


class SignallingWriter(CollectingWriter):
    """A CollectingWriter that lets a test wait until it holds ``n`` results.

    ``on_write`` (if set) runs after each result is stored, with the count so far.
    """

    def __init__(self, on_write: Callable[[int], None] | None = None) -> None:
        super().__init__()
        self._cond = threading.Condition()
        self._on_write = on_write

    def write_result(self, result: DomainCheckResult) -> None:
        with self._cond:
            super().write_result(result)
            self._cond.notify_all()
        if self._on_write is not None:
            self._on_write(len(self.results))

    def wait_for(self, n: int, timeout: float = WAIT_TIMEOUT) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: len(self.results) >= n, timeout)

    @property
    def fqdns(self) -> list[str]:
        return [r.domain.fqdn for r in self.results]


class ConcurrencyProbe:
    """Records which checks run at the same time, per host and overall."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.in_flight: dict[str, int] = {}
        self.max_in_flight: dict[str, int] = {}
        self.total = 0
        self.max_total = 0
        self.started: list[str] = []
        self.threads: set[str] = set()

    def enter(self, host: str, fqdn: str) -> None:
        with self._lock:
            self.started.append(fqdn)
            self.threads.add(threading.current_thread().name)
            self.in_flight[host] = self.in_flight.get(host, 0) + 1
            self.max_in_flight[host] = max(self.max_in_flight.get(host, 0), self.in_flight[host])
            self.total += 1
            self.max_total = max(self.max_total, self.total)

    def leave(self, host: str) -> None:
        with self._lock:
            self.in_flight[host] -= 1
            self.total -= 1


class GatedRegistrar(ScriptedRegistrar):
    """A ScriptedRegistrar for one ``host`` whose checks can be held open.

    A check of a domain (fqdn or SLD) listed in ``gates`` waits for that
    event before answering; ``barrier`` (if any) is passed by every check.
    ``entered`` is set for an fqdn as soon as its check starts. Every check
    is recorded in the shared ``probe``. No sleeps: waits end on events, or
    fail after ``WAIT_TIMEOUT``.
    """

    def __init__(
        self,
        host: str,
        probe: ConcurrencyProbe,
        script: Mapping[str, Outcome] | None = None,
        *,
        gates: Mapping[str, threading.Event] | None = None,
        barrier: threading.Barrier | None = None,
        default: Outcome = Availability.TAKEN,
    ) -> None:
        super().__init__(script, default=default, raw_title=host)
        self.host = host
        self._probe = probe
        self._gates = dict(gates or {})
        self._barrier = barrier
        self._lock = threading.Lock()
        self.entered: dict[str, threading.Event] = {}

    def started(self, fqdn: str) -> threading.Event:
        with self._lock:
            return self.entered.setdefault(fqdn, threading.Event())

    def check_availability(self, domain: DomainHack) -> DomainCheckResult:
        self._probe.enter(self.host, domain.fqdn)
        try:
            self.started(domain.fqdn).set()
            gate = self._gates.get(domain.fqdn, self._gates.get(domain.sld))
            if gate is not None and not gate.wait(WAIT_TIMEOUT):
                raise AssertionError(f"gate for {domain.fqdn} never opened")
            if self._barrier is not None:
                self._barrier.wait(WAIT_TIMEOUT)
            with self._lock:
                return super().check_availability(domain)
        finally:
            self._probe.leave(self.host)


class FakeResultCache(ResultCache):
    """An in-memory ResultCache that records lookups and stores (and their threads)."""

    def __init__(self, hits: Mapping[str, Availability] | None = None) -> None:
        self._hits = dict(hits or {})
        self.lookups: list[str] = []
        self.stored: list[str] = []
        self.threads: set[str] = set()

    def lookup(self, domain: DomainHack) -> DomainCheckResult | None:
        self.threads.add(threading.current_thread().name)
        self.lookups.append(domain.fqdn)
        availability = self._hits.get(domain.fqdn)
        if availability is None:
            return None
        return DomainCheckResult(domain=domain, availability=availability, raw_title="cache")

    def store(self, result: DomainCheckResult) -> None:
        self.threads.add(threading.current_thread().name)
        self.stored.append(result.domain.fqdn)


class FakeClock:
    """Manual clock: ``clock()``/``clock.time()`` read it, ``clock.sleep(s)`` advances it."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeRandom:
    """A scripted ``random.random``: returns ``values`` in turn, then repeats the last.

    The default 0.5 is the midpoint, i.e. no jitter in ``HostThrottle``.
    """

    def __init__(self, *values: float) -> None:
        self._values = list(values) or [0.5]
        self.calls = 0

    def __call__(self) -> float:
        value = self._values[min(self.calls, len(self._values) - 1)]
        self.calls += 1
        return value


def fake_throttle(
    clock: FakeClock, random: FakeRandom | None = None, **kwargs: Any
) -> HostThrottle:
    """A HostThrottle on ``clock``; without ``random`` its jitter is neutral (0.5)."""
    return HostThrottle(
        clock=clock.time, sleep=clock.sleep, random=random or FakeRandom(), **kwargs
    )


@dataclass(frozen=True)
class CatalogCall:
    tld: TLD
    delay: float
    breaker: HostCircuitBreaker | None
    contact: str | None


@dataclass
class FakeCatalog:
    """Stands in for ``build_registrar_for`` (pass it as ``catalog=`` to the CLI).

    Every TLD gets ``default``, unless ``by_tld`` is given: then only its
    suffixes are supported. ``raises`` makes every call raise instead.
    """

    default: RegistrarClient | None = None
    by_tld: Mapping[str, RegistrarClient] | None = None
    raises: type[BaseException] | None = None
    calls: list[CatalogCall] = field(default_factory=list)

    def __call__(
        self,
        tld: TLD,
        *,
        delay: float,
        breaker: HostCircuitBreaker | None,
        contact: str | None,
    ) -> RegistrarClient | None:
        self.calls.append(CatalogCall(tld, delay, breaker, contact))
        if self.raises is not None:
            raise self.raises()
        if self.by_tld is not None:
            return self.by_tld.get(tld.suffix)
        return self.default


def guarded_env() -> dict[str, str]:
    """Environment for a subprocess that blocks the network (see ``netguard_site``)."""
    env = dict(os.environ)
    paths = [str(NETGUARD_SITE_DIR), env.get("PYTHONPATH", "")]
    env["PYTHONPATH"] = os.pathsep.join(p for p in paths if p)
    return env


def cli_command(*args: str) -> list[str]:
    return [sys.executable, "-m", "domainhack", *args]


def run_cli(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run ``python -m domainhack ARGS`` with the network blocked."""
    return subprocess.run(
        cli_command(*args),
        capture_output=True,
        text=True,
        check=False,
        env=guarded_env(),
        **kwargs,
    )


class FakeDnsLookup(DnsLookup):
    """A ``DnsLookup`` answering from a dict keyed by fqdn; unknown names get ``default``.

    Records every queried fqdn (thread-safe). ``raises`` makes it break the
    port contract by raising, to test that callers survive it.
    """

    def __init__(
        self,
        answers: Mapping[str, DnsEvidence] | None = None,
        *,
        default: DnsEvidence | None = None,
        raises: type[Exception] | None = None,
    ) -> None:
        self._answers = dict(answers or {})
        self._default = default if default is not None else DnsEvidence()
        self._raises = raises
        self._lock = threading.Lock()
        self.calls: list[str] = []
        self.threads: set[str] = set()

    def lookup(self, fqdn: str) -> DnsEvidence:
        with self._lock:
            self.calls.append(fqdn)
            self.threads.add(threading.current_thread().name)
        if self._raises is not None:
            raise self._raises("resolver exploded")
        return self._answers.get(fqdn, self._default)


@dataclass(frozen=True)
class FakeNsRecord:
    """Stands in for a dnspython NS rdata: only ``target`` is read."""

    target: str


DnsScript = list[Any] | type[BaseException] | BaseException


class FakeDnsResolver:
    """Stands in for ``dns.resolver.Resolver``: answers ``(qname, rdtype)`` from a script.

    A script value is the list of records to return, or an exception (class
    or instance) to raise. Unscripted queries return no records (NODATA).
    Every call is recorded with its keyword arguments.
    """

    def __init__(self, script: Mapping[tuple[str, str], DnsScript] | None = None) -> None:
        self._script = dict(script or {})
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def resolve(self, qname: str, rdtype: str, **kwargs: Any) -> list[Any]:
        self.calls.append((qname, rdtype, kwargs))
        outcome = self._script.get((qname, rdtype), [])
        if isinstance(outcome, list):
            return outcome
        raise outcome
