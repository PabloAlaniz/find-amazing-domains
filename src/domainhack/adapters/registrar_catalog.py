"""Pick the best availability source for a TLD.

Priority: RDAP (hard-coded override, then IANA bootstrap, then the bundled
bootstrap snapshot) -> WHOIS_SERVERS (TLDs without usable RDAP) ->
WHOIS_FALLBACK_SERVERS (RDAP TLDs; used only if RDAP resolution yields
nothing, with a stderr warning) -> unsupported (None).

Thanks to the bundled snapshot the choice does not depend on the network:
offline and online runs pick the same backend for every catalog TLD.

``.to`` is served by RDAP too (rdap.tonicregistry.to): it is the
registry's official protocol, it agreed with the Tonic web form on 23/23
test names (including registered names without NS records), and it gives
proper status codes and Retry-After instead of scraping a form with a
spoofed browser User-Agent. ``TonicRegistrarClient`` remains available.
"""

from __future__ import annotations

import functools
import sys

from domainhack.adapters._circuit import HostCircuitBreaker
from domainhack.adapters.rdap_bootstrap import RdapBootstrap
from domainhack.adapters.rdap_registrar import RdapRegistrarClient
from domainhack.adapters.whois_registrar import (
    WHOIS_FALLBACK_SERVERS,
    WHOIS_SERVERS,
    WhoisRegistrarClient,
)
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
) -> RegistrarClient | None:
    """Return a new client able to check domains under ``tld``, or None if unsupported.

    Pass one ``breaker`` to every call of a run so clients that share a host
    (e.g. .io/.sh/.ac/.me) also share its circuit.
    """
    base_url = _default_bootstrap().base_url_for(tld.suffix)
    if base_url is not None:
        return RdapRegistrarClient(base_url, delay=delay, timeout=timeout, breaker=breaker)
    suffix = tld.suffix.lower()
    if suffix in WHOIS_SERVERS:
        return WhoisRegistrarClient(
            delay=delay, timeout=timeout, servers=WHOIS_SERVERS, breaker=breaker
        )
    if suffix in WHOIS_FALLBACK_SERVERS:
        host = WHOIS_FALLBACK_SERVERS[suffix].host
        print(
            f"warning: no RDAP server found for .{suffix}; falling back to WHOIS ({host})",
            file=sys.stderr,
        )
        return WhoisRegistrarClient(
            delay=delay, timeout=timeout, servers=WHOIS_FALLBACK_SERVERS, breaker=breaker
        )
    return None


def supported_tlds() -> set[str]:
    """All TLDs ``build_registrar_for`` can serve (may fetch the IANA bootstrap)."""
    return _default_bootstrap().known_tlds() | set(WHOIS_SERVERS) | set(WHOIS_FALLBACK_SERVERS)
