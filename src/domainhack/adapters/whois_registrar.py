"""WHOIS (port 43) availability checks for TLDs without a usable RDAP server."""

from __future__ import annotations

import re
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from domainhack.adapters._circuit import HostCircuitBreaker
from domainhack.adapters._registration import normalize_statuses, parse_datetime_utc
from domainhack.adapters._throttle import DEFAULT_THROTTLE, HostThrottle
from domainhack.domain.entities import Availability, DomainCheckResult, DomainHack
from domainhack.ports.registrar import RegistrarClient

WHOIS_PORT = 43
DEFAULT_CONNECT_TIMEOUT = 5.0
# Most registries document roughly 1 query/s per client (see registrar research).
DEFAULT_MIN_INTERVAL = 1.0
_MAX_RESPONSE_BYTES = 256 * 1024

# Best-effort registration details for TAKEN replies (formats vary by registry).
_WHOIS_STATUS = re.compile(r"^[ \t]*(?:Domain[ \t]+)?Status:[ \t]*([a-z]+(?:[A-Z][a-z]+)*)\b", re.M)
_WHOIS_EXPIRY = re.compile(
    r"^[ \t]*(?:Registry[ \t]+)?(?:Expiry|Expiration|Expire)[ \t]+Date:[ \t]*(\S[^\r\n]*?)[ \t]*$",
    re.M | re.I,
)


@dataclass(frozen=True)
class WhoisServer:
    """How to query one registry and recognise an unregistered name.

    ``taken`` is optional: when set, a response matching neither pattern is
    an ERROR instead of TAKEN. ``min_interval`` is the minimum spacing in
    seconds between queries to ``host``; the client uses the larger of it and
    its own ``delay``.
    """

    host: str
    not_found: re.Pattern[str]
    query_format: str = "{fqdn}\r\n"
    taken: re.Pattern[str] | None = None
    min_interval: float = DEFAULT_MIN_INTERVAL


def _server(
    host: str,
    not_found: str,
    query_format: str = "{fqdn}\r\n",
    taken: str | None = None,
    min_interval: float = DEFAULT_MIN_INTERVAL,
) -> WhoisServer:
    return WhoisServer(
        host=host,
        not_found=re.compile(not_found, re.MULTILINE),
        query_format=query_format,
        taken=re.compile(taken, re.MULTILINE) if taken else None,
        min_interval=min_interval,
    )


# Servers and "not found" patterns verified live (see registrar research,
# 2026-10-06). Patterns are case-sensitive on purpose: e.g. "NOT FOUND" must
# not match prose in a registered domain's legal disclaimer.
#
# min_interval: every server gets the documented ~1 query/s floor. whois.nic.it
# silently stopped answering after ~50 queries at 1 query/s in a live run
# (2026-10-06), so it is paced at 4 s.
#
# Which table a TLD is in is a routing decision (see registrar_catalog):
#
# * WHOIS_SERVERS: TLDs with no usable RDAP server (none published, or one on
#   RDAP_DENYLIST such as gg/la). WHOIS is their primary and only backend.
# * WHOIS_FALLBACK_SERVERS: TLDs that RDAP serves through the IANA bootstrap
#   (always available offline thanks to the bundled snapshot). Used only if
#   RDAP resolution ever yields nothing for them, and the catalog warns on
#   stderr when it happens, so the protocol never changes silently.
#
# TLDs pinned in RDAP_OVERRIDES (ac, co, de, io, me, sh, so, to) have no
# WHOIS entry: the override always wins, so an entry could never be reached.
# Their verified servers, for reference: whois.nic.{io,sh,ac,me}
# ("^Domain not found\."), whois.registry.co ("DOMAIN NOT FOUND"),
# whois.denic.de ("Status:\s*free" / taken "Status:\s*connect"),
# whois.nic.so ("No Object Found"), whois.tonicregistry.to
# ("is available for registration").
WHOIS_SERVERS: Mapping[str, WhoisServer] = {
    "it": _server("whois.nic.it", r"Status:\s+AVAILABLE", min_interval=4.0),
    "am": _server("whois.amnic.net", r"^No match"),
    "at": _server("whois.nic.at", r"% nothing found"),
    "be": _server("whois.dns.be", r"Status:\s+AVAILABLE"),
    "gg": _server("whois.gg", r"NOT FOUND"),
    "im": _server("whois.nic.im", r"was not found"),
    "la": _server("whois.nic.la", r"DOMAIN NOT FOUND"),
    "ma": _server("whois.registre.ma", r"No Object Found"),
    "mx": _server("whois.mx", r"Object_Not_Found"),
    "nu": _server("whois.iis.nu", r"not found\."),
    "pe": _server("kero.yachay.pe", r"Domain Status: No Object Found"),
    "st": _server("whois.nic.st", r"No entries found for domain"),
}

WHOIS_FALLBACK_SERVERS: Mapping[str, WhoisServer] = {
    "ar": _server("whois.nic.ar", r"no se encuentra registrado"),
    "fm": _server("whois.nic.fm", r"DOMAIN NOT FOUND"),
    "in": _server("whois.nixiregistry.in", r"is available for registration"),
    "is": _server("whois.isnic.is", r"No entries found for query"),
    "ly": _server("whois.nic.ly", r"No Object Found"),
    "re": _server("whois.nic.re", r"NOT FOUND"),
    "tv": _server("whois.nic.tv", r"No Data Found"),
}

ALL_WHOIS_SERVERS: Mapping[str, WhoisServer] = {**WHOIS_SERVERS, **WHOIS_FALLBACK_SERVERS}

# Responses that mean "no answer", not "registered". Many registries mention
# throttling in the legal boilerplate of every reply, so these only count when
# the reply does not contain the queried name (a real record always does).
_ERROR_PATTERNS = re.compile(
    r"rate limit|limit exceeded|exceeded (the )?(maximum|allowed|query)|"
    r"too many (requests|queries|connections)|try again later|"
    r"requests of this client are not permitted|access denied|"
    r"temporarily (unavailable|blocked)|quota exceeded",
    re.IGNORECASE,
)


# Bytes that would end or split a WHOIS query line.
_LINE_BREAKING_CHARS = frozenset("\r\n\x00")
# ...plus whitespace, which most servers read as an argument separator. A
# leading "-" is rejected separately: servers parse it as a query flag
# (e.g. "-h x", "-t dn,ace").
_UNSAFE_NAME_CHARS = _LINE_BREAKING_CHARS | frozenset(" \t\v\f")


class UnsafeWhoisQueryError(ValueError):
    """A WHOIS query name contains bytes that could alter the query."""


def check_query_name(name: str) -> None:
    """Raise UnsafeWhoisQueryError unless ``name`` is safe to send as one query.

    Rejects CR, LF, NUL, spaces/tabs, a leading ``-``, an empty name and
    non-ASCII text (names must already be A-labels).
    """
    if not name:
        raise UnsafeWhoisQueryError("empty WHOIS query")
    if name.startswith("-"):
        raise UnsafeWhoisQueryError(f"WHOIS query starts with '-': {name!r}")
    if any(c in _UNSAFE_NAME_CHARS for c in name) or not name.isascii():
        raise UnsafeWhoisQueryError(f"WHOIS query contains unsafe characters: {name!r}")


class Connection(Protocol):
    def settimeout(self, value: float | None, /) -> None: ...
    def sendall(self, data: bytes, /) -> None: ...
    def recv(self, bufsize: int, /) -> bytes: ...
    def close(self) -> None: ...


Connector = Callable[[tuple[str, int], float], Connection]


def _default_connect(address: tuple[str, int], timeout: float) -> Connection:
    return socket.create_connection(address, timeout=timeout)


def whois_query(
    host: str,
    query: str,
    timeout: float,
    connect: Connector = _default_connect,
    connect_timeout: float | None = None,
) -> str:
    """Send ``query`` to ``host:43`` and read the reply until EOF.

    ``connect_timeout`` (default: ``timeout``) bounds the TCP handshake, so an
    unreachable server fails fast; ``timeout`` bounds each read.

    ``query`` must be exactly one line: UnsafeWhoisQueryError is raised,
    before connecting, if anything but its trailing CRLF is a CR, LF or NUL.
    (Callers check the queried name itself with ``check_query_name``.)
    """
    body = query[:-2] if query.endswith("\r\n") else query
    if not body or any(c in _LINE_BREAKING_CHARS for c in body):
        raise UnsafeWhoisQueryError(f"WHOIS query is not a single line: {query!r}")
    conn = connect((host, WHOIS_PORT), timeout if connect_timeout is None else connect_timeout)
    try:
        conn.settimeout(timeout)
        conn.sendall(query.encode("utf-8"))
        chunks: list[bytes] = []
        size = 0
        while size < _MAX_RESPONSE_BYTES:
            chunk = conn.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    finally:
        conn.close()
    raw = b"".join(chunks)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


class WhoisRegistrarClient(RegistrarClient):
    """Checks availability over WHOIS, routing each domain by its TLD.

    Not-found pattern -> AVAILABLE; any other non-empty answer -> TAKEN
    (or ERROR if the server has a ``taken`` pattern that does not match);
    empty answers, socket errors, timeouts and rate-limit text -> ERROR.
    ``raw_title`` holds ``"whois <host>"``. TAKEN results carry EPP status
    codes and an ISO 8601 expiry date when the reply has them (best effort).

    Queries to one host are spaced by max(``delay``, the server's
    ``min_interval``). Empty answers, socket errors, timeouts and rate-limit
    text count as failures for the per-host circuit ``breaker``; while a
    host's circuit is open its domains get an ERROR without any network call.
    """

    def __init__(
        self,
        delay: float = 1.0,
        timeout: float = 10.0,
        servers: Mapping[str, WhoisServer] = ALL_WHOIS_SERVERS,
        connect: Connector = _default_connect,
        *,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        throttle: HostThrottle | None = None,
        breaker: HostCircuitBreaker | None = None,
    ) -> None:
        self._delay = delay
        self._timeout = timeout
        self._connect_timeout = min(connect_timeout, timeout)
        self._breaker = breaker if breaker is not None else HostCircuitBreaker()
        self._servers = servers
        self._connect = connect
        self._throttle = throttle if throttle is not None else DEFAULT_THROTTLE

    def supports(self, tld: str) -> bool:
        return tld.lower() in self._servers

    def check_availability(self, domain: DomainHack) -> DomainCheckResult:
        server = self._servers.get(domain.tld.suffix.lower())
        if server is None:
            return _error(domain, f"No WHOIS server configured for .{domain.tld.suffix}")
        title = f"whois {server.host}"
        if not self._breaker.allow(server.host):
            return _error(domain, self._breaker.skip_message(server.host), title)

        query = server.query_format.format(fqdn=domain.fqdn)
        try:
            check_query_name(domain.fqdn)
        except UnsafeWhoisQueryError as e:
            return _error(domain, f"Refused to send WHOIS query: {e}", title)

        self._throttle.wait(server.host, max(self._delay, server.min_interval))
        try:
            text = whois_query(
                server.host,
                query,
                self._timeout,
                self._connect,
                self._connect_timeout,
            )
        except UnsafeWhoisQueryError as e:
            return _error(domain, f"Refused to send WHOIS query: {e}", title)
        except (OSError, TimeoutError) as e:
            self._breaker.record_failure(server.host)
            return _error(domain, f"WHOIS query failed: {e or type(e).__name__}", title)

        if not text.strip():
            self._breaker.record_failure(server.host)
            return _error(domain, "Empty WHOIS response", title)
        if server.not_found.search(text):
            self._breaker.record_success(server.host)
            return DomainCheckResult(
                domain=domain, availability=Availability.AVAILABLE, raw_title=title
            )
        if domain.fqdn.lower() not in text.lower() and _ERROR_PATTERNS.search(text):
            self._breaker.record_failure(server.host)
            return _error(domain, "WHOIS server refused or rate-limited the query", title)
        self._breaker.record_success(server.host)
        if server.taken is not None and not server.taken.search(text):
            return _error(domain, "Unrecognised WHOIS response", title)
        return DomainCheckResult(
            domain=domain,
            availability=Availability.TAKEN,
            raw_title=title,
            statuses=parse_whois_statuses(text),
            expires_at=parse_whois_expiration(text),
        )


def parse_whois_statuses(text: str) -> tuple[str, ...]:
    """EPP status codes from ``Status:`` / ``Domain Status:`` lines, best effort.

    Only a leading camelCase EPP-style code is taken (``pendingDelete``,
    ``ok``, ``clientTransferProhibited https://icann.org/epp#...``); free
    text such as ``Status: NOT AVAILABLE`` is ignored.
    """
    return normalize_statuses(m.group(1) for m in _WHOIS_STATUS.finditer(text))


def parse_whois_expiration(text: str) -> datetime | None:
    """The first ISO 8601 expiry date (``Registry Expiry Date:``, ``Expire Date:``...), or None.

    Other date formats (``30-Nov-2026``, ``30.11.2026``...) are ignored.
    """
    for match in _WHOIS_EXPIRY.finditer(text):
        when = parse_datetime_utc(match.group(1))
        if when is not None:
            return when
    return None


def _error(domain: DomainHack, message: str, title: str = "") -> DomainCheckResult:
    return DomainCheckResult(
        domain=domain,
        availability=Availability.ERROR,
        raw_title=title,
        error_message=message,
    )
