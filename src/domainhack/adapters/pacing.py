"""Where a registrar client sends a TLD's queries, and how fast (for run estimates)."""

from __future__ import annotations

from collections.abc import Hashable

import httpx

from domainhack.adapters.rdap_registrar import RdapRegistrarClient
from domainhack.adapters.whois_registrar import WhoisRegistrarClient
from domainhack.domain.entities import TLD
from domainhack.ports.registrar import RegistrarClient
from domainhack.usecases.estimate_run import Pacing


def pacing_for(client: RegistrarClient | None, tld: TLD, delay: float) -> Pacing:
    """The host serving ``tld`` through ``client`` and its base query interval.

    RDAP clients are paced at ``delay``; WHOIS servers at max(``delay``, the
    server's ``min_interval``). An unknown client is assumed to be on a host
    of its own, paced at ``delay``.
    """
    if isinstance(client, RdapRegistrarClient):
        return Pacing(httpx.URL(client.base_url).host, delay)
    if isinstance(client, WhoisRegistrarClient):
        server = client.server_for(tld.suffix)
        if server is not None:
            return Pacing(server.host, max(delay, server.min_interval))
    return Pacing(f".{tld.suffix}", delay)


def lane_for(client: RegistrarClient | None, tld: TLD) -> Hashable:
    """The parallel-check lane for ``tld``: domains in one lane never run concurrently.

    RDAP and WHOIS clients get the registry host they query, so TLDs served
    by the same host (.io/.sh/.ac/.me) share a lane and that host never has
    more than one request in flight. Any other client gets a lane of its own
    per instance: nothing is known about its host or its thread-safety, so
    it is never called concurrently. Unsupported TLDs (no client) share a
    lane; the router answers them without network access.
    """
    if isinstance(client, (RdapRegistrarClient, WhoisRegistrarClient)):
        pacing = pacing_for(client, tld, 0.0)
        if not pacing.host.startswith("."):
            return pacing.host
    return ("client", id(client)) if client is not None else ("unsupported",)
