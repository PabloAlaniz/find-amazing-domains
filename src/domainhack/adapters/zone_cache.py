"""Answer "taken" from a zone snapshot before a name reaches a registry.

``ZoneAwareCache`` is a ``ResultCache``: the check use case consults it on its
own thread before dispatching a name, so a hit skips the registry queue
altogether. The real result cache is asked first (its answers carry
registration details); then the zone, which can only say "taken".
"""

from __future__ import annotations

from collections.abc import Mapping

from domainhack.adapters.zone_snapshot import ZoneSnapshot
from domainhack.domain.entities import Availability, DomainCheckResult, DomainHack
from domainhack.ports.result_cache import ResultCache

ZONE_SOURCE = "zone file"


class ZoneAwareCache(ResultCache):
    """``inner`` (may be None: no result cache) plus zone snapshots by TLD suffix.

    ``zone_hits`` counts the names answered from a snapshot.
    """

    def __init__(self, inner: ResultCache | None, zones: Mapping[str, ZoneSnapshot]) -> None:
        self._inner = inner
        self._zones = dict(zones)
        self.zone_hits = 0

    def lookup(self, domain: DomainHack) -> DomainCheckResult | None:
        if self._inner is not None:
            cached = self._inner.lookup(domain)
            if cached is not None:
                return cached
        zone = self._zones.get(domain.tld.suffix)
        if zone is not None and domain.ascii_sld in zone:
            self.zone_hits += 1
            return DomainCheckResult(domain, Availability.TAKEN, raw_title=ZONE_SOURCE)
        return None

    def store(self, result: DomainCheckResult) -> None:
        if self._inner is not None:
            self._inner.store(result)

    def close(self) -> None:
        for zone in self._zones.values():
            zone.close()
