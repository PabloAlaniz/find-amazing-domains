"""Second opinion from public DNS on registry answers.

The registry (RDAP/WHOIS) stays the source of truth; DNS only adds
evidence. An AVAILABLE name that is delegated in DNS is a conflict (see
``DomainCheckResult.dns_conflict``): something is wrong (a stale or broken
registry answer, a reserved name...), so it must not be reported as free.
A TAKEN name without NS records is registered but not in use.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

from domainhack.domain.entities import Availability, DnsEvidence, DomainCheckResult
from domainhack.domain.parking import parking_hint_for
from domainhack.ports.dns_lookup import DnsLookup

DEFAULT_DNS_WORKERS = 8

_CONFIRMED = frozenset({Availability.AVAILABLE, Availability.TAKEN})


class ConfirmWithDns:
    """Fills ``DomainCheckResult.dns`` for AVAILABLE and TAKEN results.

    ERROR results pass through unchanged (there is no registry answer to
    confirm). Lookups run on a pool of at most ``workers`` threads, so the
    ``lookup`` must be thread-safe.
    """

    def __init__(self, lookup: DnsLookup, workers: int = DEFAULT_DNS_WORKERS) -> None:
        if workers < 1:
            raise ValueError("workers must be at least 1")
        self._lookup = lookup
        self.workers = workers

    @staticmethod
    def applies_to(result: DomainCheckResult) -> bool:
        """True for results whose registry answer DNS can confirm (AVAILABLE, TAKEN)."""
        return result.availability in _CONFIRMED

    def confirm(self, result: DomainCheckResult) -> DomainCheckResult:
        """A copy of ``result`` with ``dns`` filled; ``result`` itself if it is an ERROR."""
        if not self.applies_to(result):
            return result
        try:
            evidence = self._lookup.lookup(result.domain.fqdn)
        except Exception as exc:  # the port says never; a broken adapter must not end the run
            evidence = DnsEvidence(error=f"DNS lookup failed: {exc or type(exc).__name__}")
        parked = result.parked_hint
        if not parked and result.availability is Availability.TAKEN and evidence.is_delegated:
            # The registry gave no nameservers (e.g. WHOIS TLDs): classify DNS's instead.
            parked = parking_hint_for(evidence.nameservers)
        return dataclasses.replace(result, dns=evidence, parked_hint=parked)

    def apply(self, results: Iterable[DomainCheckResult]) -> list[DomainCheckResult]:
        """Confirm every result, concurrently; the output keeps the input order."""
        items = list(results)
        if not any(self.applies_to(r) for r in items):
            return items
        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="dns-confirm") as pool:
            return list(pool.map(self.confirm, items))
