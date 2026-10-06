"""Pick the best availability source for a TLD.

Priority: RDAP (hard-coded override, then IANA bootstrap) -> WHOIS table ->
unsupported (None). ``.to`` is served by RDAP too (rdap.tonicregistry.to):
it is the registry's official protocol, it agreed with the Tonic web form on
23/23 test names (including registered names without NS records), and it
gives proper status codes and Retry-After instead of scraping a web form.
"""

from __future__ import annotations

import functools

from domainhack.adapters._circuit import HostCircuitBreaker
from domainhack.adapters.rdap_bootstrap import RdapBootstrap
from domainhack.adapters.rdap_registrar import RdapRegistrarClient
from domainhack.adapters.whois_registrar import WHOIS_SERVERS, WhoisRegistrarClient
from domainhack.domain.entities import TLD
from domainhack.ports.registrar import RegistrarClient


@functools.cache
def _default_bootstrap() -> RdapBootstrap:
    return RdapBootstrap()


def build_registrar_for(
    tld: TLD,
    delay: float,
    timeout: float = 10.0,
    breaker: HostCircuitBreaker | None = None,
    contact: str | None = None,
) -> RegistrarClient | None:
    """Return a new client able to check domains under ``tld``, or None if unsupported.

    Pass one ``breaker`` to every call of a run so clients that share a host
    (e.g. .io/.sh/.ac/.me) also share its circuit. ``contact`` (an email) is
    sent as the ``From`` header on RDAP requests.
    """
    base_url = _default_bootstrap().base_url_for(tld.suffix)
    if base_url is not None:
        return RdapRegistrarClient(
            base_url, delay=delay, timeout=timeout, breaker=breaker, contact=contact
        )
    if tld.suffix.lower() in WHOIS_SERVERS:
        return WhoisRegistrarClient(delay=delay, timeout=timeout, breaker=breaker)
    return None


def supported_tlds() -> set[str]:
    """All TLDs ``build_registrar_for`` can serve (may fetch the IANA bootstrap)."""
    return _default_bootstrap().known_tlds() | set(WHOIS_SERVERS)
