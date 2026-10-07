"""Registry backends per TLD, loaded from ``data/registry_sources.json``.

The JSON is the single source of truth for TLDs the IANA RDAP bootstrap does
not cover (or covers badly): RDAP overrides, the RDAP denylist, WHOIS servers
with their verified "not found" patterns, and TLDs that cannot be checked at
all, with the reason why. Keeping it as data means adding a registry is a
reviewed data change, not a code change.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

REGISTRY_SOURCES = "data/registry_sources.json"
STATUSES = frozenset({"rdap", "whois", "restricted", "unavailable"})


@dataclass(frozen=True)
class WhoisSpec:
    """A port-43 server and how to read its replies (patterns as strings)."""

    host: str
    not_found: str
    taken: str | None
    query_format: str
    min_interval: float


@dataclass(frozen=True)
class RegistrySource:
    """How one TLD is checked, or why it cannot be."""

    tld: str
    status: str
    rdap: str | None
    whois: WhoisSpec | None
    reason: str
    verified_on: str | None
    source: str


@dataclass(frozen=True)
class RegistrySources:
    tlds: Mapping[str, RegistrySource]
    rdap_denylist: frozenset[str]
    rdap_denylist_reason: str

    def get(self, tld: str) -> RegistrySource | None:
        return self.tlds.get(tld.lower())

    def rdap_overrides(self) -> dict[str, str]:
        """TLD -> RDAP base URL for registries missing from the IANA bootstrap."""
        return {t: s.rdap for t, s in self.tlds.items() if s.status == "rdap" and s.rdap}

    def whois_primary(self) -> dict[str, WhoisSpec]:
        """TLDs whose only backend is WHOIS."""
        return {t: s.whois for t, s in self.tlds.items() if s.status == "whois" and s.whois}

    def whois_fallback(self) -> dict[str, WhoisSpec]:
        """RDAP TLDs with a WHOIS server kept as a fallback."""
        return {t: s.whois for t, s in self.tlds.items() if s.status == "rdap" and s.whois}

    def unsupported_reason(self, tld: str) -> str:
        """The user-facing reason a TLD cannot be checked, or ``""`` if none is recorded."""
        source = self.get(tld)
        if source is None or source.status not in ("restricted", "unavailable"):
            return ""
        return f"{source.status}: {source.reason}"


class RegistrySourcesError(ValueError):
    """The bundled registry data is malformed (a packaging bug, caught by tests)."""


def parse_registry_sources(doc: Any) -> RegistrySources:
    """Validate and convert the JSON document; raises RegistrySourcesError."""
    if not isinstance(doc, dict) or not isinstance(doc.get("tlds"), dict):
        raise RegistrySourcesError("registry sources: missing 'tlds' object")
    tlds: dict[str, RegistrySource] = {}
    for tld, raw in doc["tlds"].items():
        tlds[tld] = _parse_entry(tld, raw)
    deny = doc.get("rdap_denylist") or {}
    return RegistrySources(
        tlds=tlds,
        rdap_denylist=frozenset(t.lower() for t in deny.get("tlds", ())),
        rdap_denylist_reason=str(deny.get("reason", "")),
    )


def _parse_entry(tld: str, raw: Any) -> RegistrySource:
    def fail(message: str) -> RegistrySourcesError:
        return RegistrySourcesError(f"registry sources: .{tld}: {message}")

    if not isinstance(raw, dict):
        raise fail("entry must be an object")
    status = raw.get("status")
    if status not in STATUSES:
        raise fail(f"unknown status {status!r}")
    rdap = raw.get("rdap")
    if rdap is not None and not (isinstance(rdap, str) and rdap.startswith("https://")):
        raise fail("rdap must be an https:// base URL")
    if isinstance(rdap, str) and not rdap.endswith("/"):
        raise fail("rdap base URL must end with '/'")
    whois = _parse_whois(raw.get("whois"), fail)
    reason = str(raw.get("reason") or "")
    if status == "whois" and whois is None:
        raise fail("status whois needs a whois server")
    if status in ("restricted", "unavailable") and not reason:
        raise fail(f"status {status} needs a reason")
    verified = raw.get("verified")
    if status in ("rdap", "whois") and not verified:
        raise fail("rdap/whois entries must record when they were verified")
    return RegistrySource(
        tld=tld.lower(),
        status=status,
        rdap=rdap,
        whois=whois,
        reason=reason,
        verified_on=verified.get("date") if isinstance(verified, dict) else None,
        source=str(raw.get("source") or ""),
    )


def _parse_whois(raw: Any, fail: Any) -> WhoisSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or not raw.get("host") or not raw.get("not_found"):
        raise fail("whois needs host and not_found")
    for key in ("not_found", "taken"):
        pattern = raw.get(key)
        if pattern is not None:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise fail(f"invalid {key} regex: {exc}") from exc
    query_format = raw.get("query_format") or "{fqdn}\r\n"
    if "{fqdn}" not in query_format or not query_format.endswith("\r\n"):
        raise fail("query_format must contain {fqdn} and end with CRLF")
    return WhoisSpec(
        host=str(raw["host"]),
        not_found=str(raw["not_found"]),
        taken=raw.get("taken"),
        query_format=query_format,
        min_interval=float(raw.get("min_interval") or 1.0),
    )


@cache
def load_registry_sources() -> RegistrySources:
    """The bundled registry data (parsed once per process)."""
    text = resources.files("domainhack").joinpath(REGISTRY_SOURCES).read_text(encoding="utf-8")
    return parse_registry_sources(json.loads(text))


def unsupported_message(suffix: str) -> str:
    """``"no registrar supports .es"``, plus the recorded reason when there is one.

    For a multi-label suffix (``com.xx``) the reason recorded for its top-level
    domain applies.
    """
    sources = load_registry_sources()
    reason = sources.unsupported_reason(suffix) or sources.unsupported_reason(
        suffix.rsplit(".", 1)[-1]
    )
    message = f"no registrar supports .{suffix}"
    return f"{message} ({reason})" if reason else message


def describe_unsupported(suffixes: Iterable[str]) -> list[str]:
    """Messages for TLDs that cannot be checked: the ones without a recorded
    reason grouped (``"no registrar supports .in, .io"``), then one message per
    TLD whose reason is known (``"no registrar supports .es (restricted: ...)"``)."""
    plain: list[str] = []
    explained: list[str] = []
    for suffix in suffixes:
        message = unsupported_message(suffix)
        if message == f"no registrar supports .{suffix}":
            plain.append(f".{suffix}")
        else:
            explained.append(message)
    grouped = [f"no registrar supports {', '.join(plain)}"] if plain else []
    return grouped + explained
