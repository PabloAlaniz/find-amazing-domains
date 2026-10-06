"""WHOIS (port 43) availability checks for TLDs without a usable RDAP server."""

from __future__ import annotations

import re
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from domainhack.adapters._throttle import DEFAULT_THROTTLE, HostThrottle
from domainhack.domain.entities import Availability, DomainCheckResult, DomainHack
from domainhack.ports.registrar import RegistrarClient

WHOIS_PORT = 43
_MAX_RESPONSE_BYTES = 256 * 1024


@dataclass(frozen=True)
class WhoisServer:
    """How to query one registry and recognise an unregistered name.

    ``taken`` is optional: when set, a response matching neither pattern is
    an ERROR instead of TAKEN.
    """

    host: str
    not_found: re.Pattern[str]
    query_format: str = "{fqdn}\r\n"
    taken: re.Pattern[str] | None = None


def _server(
    host: str, not_found: str, query_format: str = "{fqdn}\r\n", taken: str | None = None
) -> WhoisServer:
    return WhoisServer(
        host=host,
        not_found=re.compile(not_found, re.MULTILINE),
        query_format=query_format,
        taken=re.compile(taken, re.MULTILINE) if taken else None,
    )


# Servers and "not found" patterns verified live (see registrar research,
# 2026-10-06). Patterns are case-sensitive on purpose: e.g. "NOT FOUND" must
# not match prose in a registered domain's legal disclaimer.
WHOIS_SERVERS: Mapping[str, WhoisServer] = {
    "it": _server("whois.nic.it", r"Status:\s+AVAILABLE"),
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
    "fm": _server("whois.nic.fm", r"DOMAIN NOT FOUND"),
    "re": _server("whois.nic.re", r"NOT FOUND"),
    "tv": _server("whois.nic.tv", r"No Data Found"),
    "ly": _server("whois.nic.ly", r"No Object Found"),
    "so": _server("whois.nic.so", r"No Object Found"),
    "is": _server("whois.isnic.is", r"No entries found for query"),
    "in": _server("whois.nixiregistry.in", r"is available for registration"),
    "ar": _server("whois.nic.ar", r"no se encuentra registrado"),
    "co": _server("whois.registry.co", r"DOMAIN NOT FOUND"),
    "io": _server("whois.nic.io", r"^Domain not found\."),
    "sh": _server("whois.nic.sh", r"^Domain not found\."),
    "ac": _server("whois.nic.ac", r"^Domain not found\."),
    "me": _server("whois.nic.me", r"^Domain not found\."),
    "de": _server("whois.denic.de", r"Status:\s*free", taken=r"Status:\s*connect"),
    "to": _server("whois.tonicregistry.to", r"is available for registration"),
}

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


class Connection(Protocol):
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
) -> str:
    """Send ``query`` to ``host:43`` and read the reply until EOF."""
    conn = connect((host, WHOIS_PORT), timeout)
    try:
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
    ``raw_title`` holds ``"whois <host>"``.
    """

    def __init__(
        self,
        delay: float = 1.0,
        timeout: float = 10.0,
        servers: Mapping[str, WhoisServer] = WHOIS_SERVERS,
        connect: Connector = _default_connect,
        *,
        throttle: HostThrottle | None = None,
    ) -> None:
        self._delay = delay
        self._timeout = timeout
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

        self._throttle.wait(server.host, self._delay)
        try:
            text = whois_query(
                server.host,
                server.query_format.format(fqdn=domain.fqdn),
                self._timeout,
                self._connect,
            )
        except (OSError, TimeoutError) as e:
            return _error(domain, f"WHOIS query failed: {e or type(e).__name__}", title)

        if not text.strip():
            return _error(domain, "Empty WHOIS response", title)
        if server.not_found.search(text):
            return DomainCheckResult(
                domain=domain, availability=Availability.AVAILABLE, raw_title=title
            )
        if domain.fqdn.lower() not in text.lower() and _ERROR_PATTERNS.search(text):
            return _error(domain, "WHOIS server refused or rate-limited the query", title)
        if server.taken is not None and not server.taken.search(text):
            return _error(domain, "Unrecognised WHOIS response", title)
        return DomainCheckResult(domain=domain, availability=Availability.TAKEN, raw_title=title)


def _error(domain: DomainHack, message: str, title: str = "") -> DomainCheckResult:
    return DomainCheckResult(
        domain=domain,
        availability=Availability.ERROR,
        raw_title=title,
        error_message=message,
    )
