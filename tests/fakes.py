"""Shared test doubles and helpers."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from domainhack.adapters._circuit import HostCircuitBreaker
from domainhack.adapters._throttle import HostThrottle
from domainhack.domain.entities import TLD, Availability, DomainCheckResult, DomainHack
from domainhack.ports.registrar import RegistrarClient
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
