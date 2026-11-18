"""The ``_for-sale`` DNS record: parsing, lookup and where it shows up."""

from dataclasses import dataclass
from datetime import datetime, timezone

import dns.exception
import dns.resolver
import pytest

from domainhack.adapters.brand_report_render import report_to_dict
from domainhack.adapters.console_writer import ConsoleResultWriter
from domainhack.adapters.dns_resolver import DnsPythonLookup
from domainhack.adapters.json_writer import result_record
from domainhack.domain.entities import (
    TLD,
    Availability,
    DnsEvidence,
    DomainCheckResult,
    DomainHack,
)
from domainhack.domain.for_sale import LISTED, MAX_CONTENT_LENGTH, for_sale_content
from domainhack.usecases.brand_candidates import BrandCandidate, CandidateKind
from domainhack.usecases.brand_report import build_brand_report
from tests.fakes import FakeDnsResolver, FakeNsRecord

NAME = "sumanda.com."
NODE = "_for-sale.sumanda.com."
NS = [FakeNsRecord("ns1.example.net.")]


@dataclass(frozen=True)
class FakeTxtRecord:
    """Stands in for a dnspython TXT rdata: only ``strings`` is read."""

    strings: tuple[bytes, ...]


class TestContent:
    @pytest.mark.parametrize(
        ("records", "expected"),
        [
            ([], ""),
            (["v=spf1 -all", "hello"], ""),
            (["v=FORSALE1;"], LISTED),
            (["  v=forsale1;   "], LISTED),
            (["v=FORSALE1;furi=https://example.com/buy"], "furi=https://example.com/buy"),
            (["v=spf1 -all", "v=FORSALE1;fval=EUR999"], "fval=EUR999"),
            (["v=FORSALE1;ftxt=first", "v=FORSALE1;ftxt=second"], "ftxt=first"),
            (["v=FORSALE2;ftxt=later version"], ""),
            (["FORSALE1;ftxt=no tag"], ""),
        ],
    )
    def test_reads_the_first_record_with_the_version_tag(
        self, records: list[str], expected: str
    ) -> None:
        assert for_sale_content(records) == expected

    def test_the_holders_text_is_bounded_and_kept_on_one_line(self) -> None:
        content = for_sale_content(["v=FORSALE1;ftxt=a\x1b[31mb\nc" + "x" * 500])
        assert content.startswith("ftxt=a[31mbc")
        assert len(content) == MAX_CONTENT_LENGTH
        assert content.isprintable()


class TestLookup:
    def test_delegated_name_with_a_record(self) -> None:
        resolver = FakeDnsResolver(
            {
                (NAME, "NS"): NS,
                (NODE, "TXT"): [
                    FakeTxtRecord((b"v=spf1 -all",)),
                    FakeTxtRecord((b"v=FORSALE1;furi=https://", b"example.com/buy")),
                ],
            }
        )
        evidence = DnsPythonLookup(resolver).lookup("sumanda.com")
        assert evidence.for_sale == "furi=https://example.com/buy"
        assert evidence.is_delegated

    def test_plain_strings_are_read_too(self) -> None:
        resolver = FakeDnsResolver({(NAME, "NS"): NS, (NODE, "TXT"): ["v=FORSALE1;"]})
        assert DnsPythonLookup(resolver).lookup("sumanda.com").for_sale == LISTED

    @pytest.mark.parametrize("outcome", [dns.resolver.NXDOMAIN, dns.exception.Timeout, []])
    def test_no_record_or_a_failed_query_leaves_it_empty(self, outcome: object) -> None:
        resolver = FakeDnsResolver({(NAME, "NS"): NS, (NODE, "TXT"): outcome})  # type: ignore[dict-item]
        evidence = DnsPythonLookup(resolver).lookup("sumanda.com")
        assert evidence == DnsEvidence(nameservers=("ns1.example.net",))

    def test_a_name_without_nameservers_is_not_asked(self) -> None:
        resolver = FakeDnsResolver({(NODE, "TXT"): ["v=FORSALE1;"]})
        assert DnsPythonLookup(resolver).lookup("sumanda.com").for_sale == ""
        assert "TXT" not in [call[1] for call in resolver.calls]

    def test_undecodable_bytes_do_not_break_the_lookup(self) -> None:
        resolver = FakeDnsResolver(
            {(NAME, "NS"): NS, (NODE, "TXT"): [FakeTxtRecord((b"v=FORSALE1;ftxt=\xff\xfe",))]}
        )
        assert DnsPythonLookup(resolver).lookup("sumanda.com").for_sale.startswith("ftxt=")


def _taken(for_sale: str, parked: str = "") -> DomainCheckResult:
    return DomainCheckResult(
        DomainHack.from_sld("sumanda", TLD("com")),
        Availability.TAKEN,
        expires_at=datetime(2026, 11, 12, tzinfo=timezone.utc),
        parked_hint=parked,
        dns=DnsEvidence(nameservers=("ns1.example.net",), for_sale=for_sale),
    )


class TestOutput:
    def test_console(self, capsys: pytest.CaptureFixture[str]) -> None:
        ConsoleResultWriter(show_taken=True).write_result(_taken("fval=USD500"))
        assert capsys.readouterr().out == (
            "  TAKEN:     sumanda.com (expires 2026-11-12, for sale: fval=USD500)\n"
        )

    def test_records(self) -> None:
        assert result_record(_taken("fval=USD500"))["for_sale"] == "fval=USD500"
        assert result_record(_taken(""))["for_sale"] == ""
        no_dns = DomainCheckResult(DomainHack.from_sld("a", TLD("to")), Availability.TAKEN)
        assert result_record(no_dns)["for_sale"] == ""

    @pytest.mark.parametrize(
        ("for_sale", "parked", "advice"),
        [
            ("listed", "sedo", "the owner lists it for sale (_for-sale DNS record)"),
            ("", "sedo", "parked at sedo: may be purchasable from the owner"),
        ],
    )
    def test_brand_report(self, for_sale: str, parked: str, advice: str) -> None:
        result = _taken(for_sale, parked)
        candidate = BrandCandidate(result.domain, CandidateKind.EXACT, "sumanda")
        report = build_brand_report(
            "sumanda",
            [candidate],
            [result],
            tlds=[TLD("com")],
            suffix_hack_found=False,
            variants_requested=False,
        )
        assert ("for sale: listed" in report.taken[0].detail) is bool(for_sale)
        assert report.advice == (f"sumanda.com is taken (expires 2026-11-12); {advice}",)
        assert report_to_dict(report)["taken"][0]["dns"]["for_sale"] == for_sale
