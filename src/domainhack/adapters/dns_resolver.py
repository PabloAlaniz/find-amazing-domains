"""Public DNS evidence for a name, via dnspython (a second opinion, never the verdict).

``DnsPythonLookup.lookup("sumanda.com")`` asks the configured recursive
resolver (the system's by default) for the name's NS records and, when the
name exists, whether it has an A or AAAA record:

- NXDOMAIN: the name does not exist in DNS -> empty evidence, no error.
- NS present: the name is delegated, so it is certainly registered. NS hosts
  are lowercase, without the trailing dot, sorted.
- Timeout, SERVFAIL (every server failed) or any other failure of the NS
  query -> ``error`` is set and the other fields mean nothing.

A failed A/AAAA query after a successful NS query only leaves
``has_address`` False: the delegation, the part that matters, is known.
``lookup`` never raises.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from typing import Any, Protocol

import dns.exception
import dns.resolver

from domainhack.domain.entities import DnsEvidence
from domainhack.ports.dns_lookup import DnsLookup

DEFAULT_DNS_TIMEOUT = 3.0


class Resolver(Protocol):
    """The part of ``dns.resolver.Resolver`` this adapter uses (tests pass a fake)."""

    def resolve(
        self,
        qname: str,
        rdtype: str,
        *,
        raise_on_no_answer: bool,
        lifetime: float | None,
        search: bool | None,
    ) -> Iterable[Any]: ...


class DnsPythonLookup(DnsLookup):
    """``DnsLookup`` backed by dnspython.

    ``resolver`` is used as given (shared by every thread); by default each
    thread gets its own ``dns.resolver.Resolver()`` built from the system
    configuration, so parallel callers never share mutable resolver state.
    ``timeout`` bounds each query (NS, A, AAAA) in seconds.
    """

    def __init__(
        self,
        resolver: Resolver | None = None,
        timeout: float = DEFAULT_DNS_TIMEOUT,
        resolver_factory: Callable[[], Resolver] = dns.resolver.Resolver,
    ) -> None:
        self._shared = resolver
        self._factory = resolver_factory
        self._timeout = timeout
        self._local = threading.local()

    def _resolver(self) -> Resolver:
        if self._shared is not None:
            return self._shared
        resolver: Resolver | None = getattr(self._local, "resolver", None)
        if resolver is None:
            resolver = self._factory()
            self._local.resolver = resolver
        return resolver

    def lookup(self, fqdn: str) -> DnsEvidence:
        name = fqdn.strip().rstrip(".").lower() + "."
        try:
            resolver = self._resolver()
            records = self._query(resolver, name, "NS")
        except dns.resolver.NXDOMAIN:
            return DnsEvidence()
        except Exception as exc:  # never raise: DNS is only a second opinion
            return DnsEvidence(error=describe_dns_error(exc))
        hosts = (_host(record) for record in records)
        nameservers = tuple(sorted({host for host in hosts if host}))
        return DnsEvidence(nameservers=nameservers, has_address=self._has_address(resolver, name))

    def _has_address(self, resolver: Resolver, name: str) -> bool:
        for rdtype in ("A", "AAAA"):
            try:
                if any(True for _ in self._query(resolver, name, rdtype)):
                    return True
            except Exception:  # NXDOMAIN, timeout...: no address known
                return False
        return False

    def _query(self, resolver: Resolver, name: str, rdtype: str) -> list[Any]:
        answer = resolver.resolve(
            name, rdtype, raise_on_no_answer=False, lifetime=self._timeout, search=False
        )
        return list(answer)


def _host(record: Any) -> str:
    """An NS record's target, lowercase, without the trailing dot."""
    target = getattr(record, "target", record)
    return str(target).strip().rstrip(".").lower()


def describe_dns_error(exc: BaseException) -> str:
    """A short, stable description of a failed DNS query."""
    if isinstance(exc, dns.exception.Timeout):
        return "DNS timeout"
    if isinstance(exc, dns.resolver.NoNameservers):
        return "DNS SERVFAIL (no nameserver answered)"
    if isinstance(exc, dns.resolver.NoResolverConfiguration):
        return "DNS not configured (no resolver found)"
    detail = str(exc) or type(exc).__name__
    return f"DNS error: {detail}"
