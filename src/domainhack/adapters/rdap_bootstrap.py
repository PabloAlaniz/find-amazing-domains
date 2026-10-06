"""Resolve a TLD to its RDAP base URL.

Order: denylist (known-broken servers) -> hard-coded overrides (working
servers missing from IANA's bootstrap) -> IANA bootstrap file
(https://data.iana.org/rdap/dns.json), fetched lazily and cached on disk.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

from domainhack.adapters._http import identity_headers

IANA_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
DEFAULT_TTL_SECONDS: float = 24 * 60 * 60

_IDENTITY_DIGITAL = "https://rdap.identitydigital.services/rdap/"

# Verified live; none of these are in the IANA bootstrap except "to", which is
# pinned so the most important TLD works even if the bootstrap fetch fails.
RDAP_OVERRIDES: Mapping[str, str] = {
    "io": _IDENTITY_DIGITAL,
    "sh": _IDENTITY_DIGITAL,
    "ac": _IDENTITY_DIGITAL,
    "me": _IDENTITY_DIGITAL,
    "co": "https://rdap.registry.co/co/",
    "de": "https://rdap.denic.de/",
    "ch": "https://rdap.nic.ch/",
    "li": "https://rdap.nic.ch/",
    "so": "https://rdap.nic.so/",
    "ws": "https://rdap.website.ws/",
    "to": "https://rdap.tonicregistry.to/rdap/",
}

# rdap.gg answers every name with an HTML 200 page; rdap.centralnic.com/la/
# answers 404 for everything (even google.la). Both go to WHOIS instead.
RDAP_DENYLIST: frozenset[str] = frozenset({"gg", "la"})

# Aggregators/redirectors are never a source of truth (rdap.org says
# "No RDAP service" for .io, which would read as AVAILABLE).
_DENIED_HOSTS: frozenset[str] = frozenset({"rdap.org", "www.rdap.org"})

Fetcher = Callable[[], bytes]


def default_bootstrap_cache_path() -> Path:
    """Return ``$XDG_CACHE_HOME/domainhack/rdap_dns.json`` (or ``~/.cache/...``)."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "domainhack" / "rdap_dns.json"


def fetch_iana_bootstrap(timeout: float = 10.0) -> bytes:
    response = httpx.get(
        IANA_BOOTSTRAP_URL, headers=identity_headers(), timeout=timeout, follow_redirects=True
    )
    response.raise_for_status()
    return response.content


def parse_bootstrap(data: Any) -> dict[str, str]:
    """Map each TLD in an RFC 9224 bootstrap document to its preferred base URL."""
    result: dict[str, str] = {}
    services = data.get("services", []) if isinstance(data, dict) else []
    for service in services:
        if not (isinstance(service, list) and len(service) >= 2):
            continue
        tlds, urls = service[0], service[1]
        if not (isinstance(tlds, list) and isinstance(urls, list)):
            continue
        url = _pick_url([u for u in urls if isinstance(u, str)])
        if url is None:
            continue
        for tld in tlds:
            if isinstance(tld, str):
                result[tld.lower()] = url
    return result


def _pick_url(urls: list[str]) -> str | None:
    usable = [u for u in urls if httpx.URL(u).host not in _DENIED_HOSTS]
    https = [u for u in usable if u.startswith("https://")]
    candidates = https or usable
    if not candidates:
        return None
    chosen = candidates[0]
    return chosen if chosen.endswith("/") else chosen + "/"


class RdapBootstrap:
    """TLD -> RDAP base URL resolver with a lazily loaded, disk-cached IANA file.

    A failed fetch falls back to a stale cache if one exists; otherwise only
    the overrides are available. The fetch is attempted at most once per
    instance, so a network outage does not cost one timeout per TLD.
    """

    def __init__(
        self,
        cache_path: Path | None = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        fetcher: Fetcher | None = None,
        clock: Callable[[], float] = time.time,
        overrides: Mapping[str, str] = RDAP_OVERRIDES,
        denylist: frozenset[str] = RDAP_DENYLIST,
    ) -> None:
        self._cache_path = cache_path if cache_path is not None else default_bootstrap_cache_path()
        self._ttl = ttl_seconds
        self._fetcher: Fetcher = fetcher if fetcher is not None else fetch_iana_bootstrap
        self._clock = clock
        self._overrides = {k.lower(): v for k, v in overrides.items()}
        self._denylist = frozenset(t.lower() for t in denylist)
        self._services: dict[str, str] | None = None

    def base_url_for(self, tld: str) -> str | None:
        key = tld.lower().lstrip(".")
        if key in self._denylist:
            return None
        if key in self._overrides:
            return self._overrides[key]
        return self._load().get(key)

    def known_tlds(self) -> set[str]:
        tlds = set(self._overrides) | set(self._load())
        return tlds - self._denylist

    def _load(self) -> dict[str, str]:
        if self._services is not None:
            return self._services

        cached = self._read_cache()
        if cached is not None and self._clock() - cached[0] <= self._ttl:
            self._services = parse_bootstrap(cached[1])
            return self._services

        try:
            raw = self._fetcher()
            data = json.loads(raw)
            services = parse_bootstrap(data)
            if not services:
                raise ValueError("empty RDAP bootstrap")
        except (httpx.HTTPError, OSError, ValueError):
            self._services = parse_bootstrap(cached[1]) if cached is not None else {}
            return self._services

        self._write_cache(data)
        self._services = services
        return services

    def _read_cache(self) -> tuple[float, Any] | None:
        try:
            wrapper = json.loads(self._cache_path.read_text(encoding="utf-8"))
            return float(wrapper["fetched_at"]), wrapper["data"]
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write_cache(self, data: Any) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._cache_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"fetched_at": self._clock(), "data": data}), encoding="utf-8"
            )
            tmp.replace(self._cache_path)
        except OSError:
            pass
