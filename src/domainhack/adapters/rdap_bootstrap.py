"""Resolve a TLD to its RDAP base URL.

Order: denylist (known-broken servers) -> hard-coded overrides (working
servers missing from IANA's bootstrap) -> IANA bootstrap file
(https://data.iana.org/rdap/dns.json), fetched lazily and cached on disk
for ``ttl_seconds`` -> the snapshot of that file bundled with the package
(``domainhack/data/rdap_dns.json``).

The bundled snapshot makes the backend choice independent of the network:
offline, every TLD resolves exactly as it did when the snapshot was taken,
and a fresh bootstrap only adds or updates entries on top of it. When the
refresh fails, one warning goes to stderr and the snapshot alone is used; a
stale on-disk cache is deliberately *not* used, so offline runs are
deterministic whatever happens to be in ``~/.cache``.

Refresh the snapshot by downloading the IANA file and re-serialising it
minified (``json.dump(data, f, separators=(",", ":"))``) into
``src/domainhack/data/rdap_dns.json``. It is kept whole (about 32 KB) so any
TLD, not just the catalog's, resolves offline exactly as online.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from importlib import resources
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
SnapshotLoader = Callable[[], Any]
Warn = Callable[[str], None]

BUNDLED_SNAPSHOT = "data/rdap_dns.json"


def load_bundled_snapshot() -> Any:
    """The IANA bootstrap document shipped as package data."""
    resource = resources.files("domainhack").joinpath(BUNDLED_SNAPSHOT)
    return json.loads(resource.read_text(encoding="utf-8"))


def snapshot_date(data: Any) -> str:
    """``YYYY-MM-DD`` from a bootstrap document's ``publication`` field, or ``unknown``."""
    publication = data.get("publication") if isinstance(data, dict) else None
    return publication[:10] if isinstance(publication, str) and publication else "unknown"


def _stderr_warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


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

    Lookups go overrides -> fresh bootstrap (disk cache younger than the TTL,
    or a new fetch) -> bundled snapshot. A failed fetch emits one warning
    through ``warn`` and leaves the snapshot as the only source. The fetch is
    attempted at most once per instance, so a network outage does not cost
    one timeout per TLD.
    """

    def __init__(
        self,
        cache_path: Path | None = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        fetcher: Fetcher | None = None,
        clock: Callable[[], float] = time.time,
        overrides: Mapping[str, str] = RDAP_OVERRIDES,
        denylist: frozenset[str] = RDAP_DENYLIST,
        snapshot: SnapshotLoader = load_bundled_snapshot,
        warn: Warn = _stderr_warn,
    ) -> None:
        self._cache_path = cache_path if cache_path is not None else default_bootstrap_cache_path()
        self._ttl = ttl_seconds
        self._fetcher: Fetcher = fetcher if fetcher is not None else fetch_iana_bootstrap
        self._clock = clock
        self._overrides = {k.lower(): v for k, v in overrides.items()}
        self._denylist = frozenset(t.lower() for t in denylist)
        self._snapshot_loader = snapshot
        self._warn = warn
        self._services: dict[str, str] | None = None

    def base_url_for(self, tld: str) -> str | None:
        """The RDAP base URL for ``tld``, or None.

        A multi-label suffix (``com.ar``) is served by the registry of its
        top-level domain (``ar`` -> https://rdap.nic.ar/) unless it has an
        entry of its own; the query still names the whole domain
        (``{base}domain/sumanda.com.ar``).
        """
        key = tld.lower().lstrip(".")
        url = self._resolve(key)
        if url is None and "." in key:
            url = self._resolve(key.rsplit(".", 1)[1])
        return url

    def _resolve(self, key: str) -> str | None:
        if key in self._denylist:
            return None
        if key in self._overrides:
            return self._overrides[key]
        return self._load().get(key)

    def known_tlds(self) -> set[str]:
        tlds = set(self._overrides) | set(self._load())
        return tlds - self._denylist

    def _load(self) -> dict[str, str]:
        if self._services is None:
            snapshot_data = self._snapshot_loader()
            services = parse_bootstrap(snapshot_data)
            services.update(self._load_fresh(snapshot_date(snapshot_data)))
            self._services = services
        return self._services

    def _load_fresh(self, snapshot_day: str) -> dict[str, str]:
        """Entries from a fresh cache or a new fetch; {} (with a warning) on failure."""
        cached = self._read_cache()
        if cached is not None and self._clock() - cached[0] <= self._ttl:
            return parse_bootstrap(cached[1])

        try:
            raw = self._fetcher()
            data = json.loads(raw)
            services = parse_bootstrap(data)
            if not services:
                raise ValueError("empty RDAP bootstrap")
        except (httpx.HTTPError, OSError, ValueError) as exc:
            reason = str(exc) or type(exc).__name__
            self._warn(
                f"could not refresh RDAP bootstrap ({reason}); "
                f"using bundled snapshot from {snapshot_day}"
            )
            return {}

        self._write_cache(data)
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
