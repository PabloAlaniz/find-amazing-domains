"""Routes each domain to the RegistrarClient responsible for its TLD."""

from __future__ import annotations

from collections.abc import Callable

from domainhack.domain.entities import TLD, Availability, DomainCheckResult, DomainHack
from domainhack.ports.registrar import RegistrarClient

RegistrarFactory = Callable[[TLD], RegistrarClient | None]


class RegistrarRouter(RegistrarClient):
    """A RegistrarClient that dispatches by TLD to per-TLD clients.

    Clients are created lazily through ``factory`` the first time a TLD is seen
    and memoized (including "no client" answers, so the factory runs at most
    once per TLD). Domains whose TLD has no client get an ERROR result without
    any network access.
    """

    def __init__(self, factory: RegistrarFactory) -> None:
        self._factory = factory
        self._clients: dict[TLD, RegistrarClient | None] = {}

    def _client_for(self, tld: TLD) -> RegistrarClient | None:
        if tld not in self._clients:
            self._clients[tld] = self._factory(tld)
        return self._clients[tld]

    def supports(self, tld: TLD) -> bool:
        """True if some registrar client can check domains under ``tld``."""
        return self._client_for(tld) is not None

    def check_availability(self, domain: DomainHack) -> DomainCheckResult:
        client = self._client_for(domain.tld)
        if client is None:
            return DomainCheckResult(
                domain=domain,
                availability=Availability.ERROR,
                error_message=f"No registrar supports .{domain.tld.suffix}",
            )
        return client.check_availability(domain)

    def close(self) -> None:
        """Close every client created so far; re-raise the first failure, if any."""
        clients = [c for c in self._clients.values() if c is not None]
        self._clients.clear()
        first_error: Exception | None = None
        for client in clients:
            try:
                client.close()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error
