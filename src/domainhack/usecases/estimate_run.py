"""Estimate how many queries a run sends and how long it takes, per host."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from domainhack.domain.entities import TLD

# Above this many queries a brute-force run needs an explicit --yes: registry
# terms of use ask clients not to bulk-query, and 10,000 queries is ~3 h at 1 query/s.
GUARDRAIL_MAX_QUERIES = 10_000


@dataclass(frozen=True)
class Pacing:
    """Where a TLD's queries go and the minimum spacing between them there."""

    host: str
    interval: float


@dataclass(frozen=True)
class HostLoad:
    host: str
    queries: int
    interval: float

    @property
    def seconds(self) -> float:
        return self.queries * self.interval


@dataclass(frozen=True)
class RunEstimate:
    hosts: tuple[HostLoad, ...]

    @property
    def queries(self) -> int:
        return sum(h.queries for h in self.hosts)

    @property
    def seconds(self) -> float:
        """Lower bound on the run time: the busiest host's queries x its interval.

        Hosts are paced independently, so requests to different hosts do not
        wait for each other; the slowest host sets the pace. Network latency
        and adaptive slow-downs only add to this.
        """
        return max((h.seconds for h in self.hosts), default=0.0)

    def exceeds(self, threshold: int = GUARDRAIL_MAX_QUERIES) -> bool:
        return self.queries > threshold


def estimate_run(queries_by_tld: Mapping[TLD, int], pacing: Callable[[TLD], Pacing]) -> RunEstimate:
    """Group per-TLD query counts by host (TLDs can share one, e.g. .io/.sh)."""
    loads: dict[str, tuple[int, float]] = {}
    for tld, queries in queries_by_tld.items():
        p = pacing(tld)
        count, interval = loads.get(p.host, (0, 0.0))
        loads[p.host] = (count + queries, max(interval, p.interval))
    return RunEstimate(
        tuple(HostLoad(host, count, interval) for host, (count, interval) in loads.items())
    )


def format_duration(seconds: float) -> str:
    """``"45 s"``, ``"12 min"``, ``"5 h 4 min"``, ``"5 d 12 h"``."""
    total = round(seconds)
    if total < 60:
        return f"{total} s"
    minutes, _ = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes} min"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h"
