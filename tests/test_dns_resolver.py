"""DnsPythonLookup against a fake dnspython resolver (no network)."""

import threading
from collections.abc import Mapping

import dns.exception
import dns.resolver
import pytest

from domainhack.adapters.dns_resolver import (
    DEFAULT_DNS_TIMEOUT,
    DnsPythonLookup,
    describe_dns_error,
)
from domainhack.domain.entities import DnsEvidence
from tests.fakes import DnsScript, FakeDnsResolver, FakeNsRecord

NAME = "sumanda.com."


def _lookup(
    script: Mapping[tuple[str, str], DnsScript], timeout: float = DEFAULT_DNS_TIMEOUT
) -> tuple[DnsPythonLookup, FakeDnsResolver]:
    resolver = FakeDnsResolver(script)
    return DnsPythonLookup(resolver, timeout=timeout), resolver


class TestLookup:
    def test_nxdomain_is_empty_evidence(self) -> None:
        lookup, resolver = _lookup({(NAME, "NS"): dns.resolver.NXDOMAIN})
        evidence = lookup.lookup("sumanda.com")
        assert evidence == DnsEvidence()
        assert not evidence.is_delegated
        assert [c[:2] for c in resolver.calls] == [(NAME, "NS")]

    def test_ns_present(self) -> None:
        lookup, _ = _lookup(
            {
                (NAME, "NS"): [
                    FakeNsRecord("NS2.DomainRecover.com."),
                    FakeNsRecord("ns1.domainrecover.com."),
                    FakeNsRecord("ns1.domainrecover.com"),
                ],
                (NAME, "A"): ["203.0.113.7"],
            }
        )
        evidence = lookup.lookup("Sumanda.com.")
        assert evidence.nameservers == ("ns1.domainrecover.com", "ns2.domainrecover.com")
        assert evidence.has_address
        assert evidence.error == ""
        assert evidence.is_delegated

    def test_aaaa_only(self) -> None:
        lookup, resolver = _lookup(
            {(NAME, "NS"): [FakeNsRecord("ns.example.")], (NAME, "AAAA"): ["2001:db8::1"]}
        )
        assert lookup.lookup("sumanda.com").has_address
        assert [c[1] for c in resolver.calls] == ["NS", "A", "AAAA"]

    def test_no_records_at_all(self) -> None:
        lookup, _ = _lookup({})
        assert lookup.lookup("sumanda.com") == DnsEvidence()

    def test_address_failure_keeps_the_delegation(self) -> None:
        lookup, _ = _lookup(
            {(NAME, "NS"): [FakeNsRecord("ns.example.")], (NAME, "A"): dns.exception.Timeout}
        )
        assert lookup.lookup("sumanda.com") == DnsEvidence(nameservers=("ns.example",))

    @pytest.mark.parametrize(
        ("exc", "message"),
        [
            (dns.resolver.LifetimeTimeout, "DNS timeout"),
            (dns.exception.Timeout, "DNS timeout"),
            (dns.resolver.NoNameservers, "DNS SERVFAIL (no nameserver answered)"),
            (dns.resolver.NoResolverConfiguration, "DNS not configured (no resolver found)"),
            (dns.resolver.YXDOMAIN, "DNS error: "),
            (RuntimeError("socket broke"), "DNS error: socket broke"),
            (RuntimeError, "DNS error: RuntimeError"),
        ],
    )
    def test_failures_go_in_error_and_never_raise(
        self, exc: type[BaseException] | BaseException, message: str
    ) -> None:
        lookup, _ = _lookup({(NAME, "NS"): exc})
        evidence = lookup.lookup("sumanda.com")
        assert evidence.error.startswith(message)
        assert evidence.nameservers == ()
        assert not evidence.is_delegated

    def test_query_options(self) -> None:
        lookup, resolver = _lookup({}, timeout=1.5)
        lookup.lookup("sumanda.com")
        assert resolver.calls[0][2] == {
            "raise_on_no_answer": False,
            "lifetime": 1.5,
            "search": False,
        }

    def test_describe_unknown_error(self) -> None:
        assert describe_dns_error(ValueError()) == "DNS error: ValueError"


class TestResolverPerThread:
    def test_default_factory_builds_one_resolver_per_thread(self) -> None:
        built: list[FakeDnsResolver] = []

        def factory() -> FakeDnsResolver:
            resolver = FakeDnsResolver()
            built.append(resolver)
            return resolver

        lookup = DnsPythonLookup(resolver_factory=factory)
        lookup.lookup("a.com")
        lookup.lookup("b.com")
        thread = threading.Thread(target=lookup.lookup, args=("c.com",))
        thread.start()
        thread.join()
        assert len(built) == 2
        # NS, A, AAAA for each name on the main thread's resolver.
        assert [c[0] for c in built[0].calls] == ["a.com."] * 3 + ["b.com."] * 3
        assert [c[0] for c in built[1].calls] == ["c.com."] * 3

    def test_factory_failure_is_an_error(self) -> None:
        def factory() -> FakeDnsResolver:
            raise dns.resolver.NoResolverConfiguration

        evidence = DnsPythonLookup(resolver_factory=factory).lookup("a.com")
        assert evidence.error == "DNS not configured (no resolver found)"
