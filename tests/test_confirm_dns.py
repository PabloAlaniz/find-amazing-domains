"""ConfirmWithDns, the DNS-confirming writer, ``check --confirm-dns`` and registry warnings."""

import threading
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

import pytest

from domainhack.adapters.console_writer import ConsoleResultWriter
from domainhack.adapters.dns_confirming_writer import DnsConfirmingWriter
from domainhack.adapters.dns_resolver import DnsPythonLookup
from domainhack.cli.app import EXIT_FAILURE, EXIT_OK, _confirming_writer, build_parser, main
from domainhack.domain.entities import Availability, DnsEvidence, DomainCheckResult
from domainhack.usecases.check_domains import CheckSummary, TldTally
from domainhack.usecases.confirm_dns import ConfirmWithDns
from tests.fakes import CollectingWriter, FakeCatalog, FakeDnsLookup, ScriptedRegistrar, hack

DELEGATED = DnsEvidence(nameservers=("ns1.example",), has_address=True)


def _result(sld: str, availability: Availability, tld: str = "to") -> DomainCheckResult:
    return DomainCheckResult(domain=hack(sld, tld), availability=availability)


class TestConfirmWithDns:
    def test_fills_dns_for_available_and_taken_only(self) -> None:
        lookup = FakeDnsLookup({"bb.to": DELEGATED})
        inputs = [
            _result("aa", Availability.AVAILABLE),
            _result("bb", Availability.TAKEN),
            _result("cc", Availability.ERROR),
        ]
        out = ConfirmWithDns(lookup).apply(inputs)
        assert [r.domain.fqdn for r in out] == ["aa.to", "bb.to", "cc.to"]
        assert out[0].dns == DnsEvidence()
        assert out[1].dns == DELEGATED
        assert out[2] is inputs[2]
        assert sorted(lookup.calls) == ["aa.to", "bb.to"]
        assert inputs[0].dns is None  # copies, not mutation

    def test_keeps_input_order_under_concurrency(self) -> None:
        names = [f"n{i:02d}" for i in range(40)]
        out = ConfirmWithDns(FakeDnsLookup(), workers=4).apply(
            _result(n, Availability.AVAILABLE) for n in names
        )
        assert [r.domain.sld for r in out] == names

    def test_bounded_pool(self) -> None:
        lookup = FakeDnsLookup()
        ConfirmWithDns(lookup, workers=2).apply(
            [_result(f"x{i}", Availability.TAKEN) for i in range(10)]
        )
        assert len(lookup.threads) <= 2
        assert all(t.startswith("dns-confirm") for t in lookup.threads)

    def test_only_errors_needs_no_pool(self) -> None:
        lookup = FakeDnsLookup()
        inputs = [_result("aa", Availability.ERROR)]
        assert ConfirmWithDns(lookup).apply(inputs) == inputs
        assert lookup.calls == []

    def test_a_raising_lookup_becomes_an_error(self) -> None:
        out = ConfirmWithDns(FakeDnsLookup(raises=RuntimeError)).apply(
            [_result("aa", Availability.AVAILABLE)]
        )
        assert out[0].dns == DnsEvidence(error="DNS lookup failed: resolver exploded")

    def test_rejects_zero_workers(self) -> None:
        with pytest.raises(ValueError):
            ConfirmWithDns(FakeDnsLookup(), workers=0)

    def test_conflict(self) -> None:
        [out] = ConfirmWithDns(FakeDnsLookup(default=DELEGATED)).apply(
            [_result("aa", Availability.AVAILABLE)]
        )
        assert out.dns_conflict

    def test_taken_without_registry_ns_gets_parking_hint_from_dns(self) -> None:
        parked = DnsEvidence(nameservers=("ns1.domainrecover.com", "ns2.domainrecover.com"))
        lookup = FakeDnsLookup({"bb.to": parked})
        out = ConfirmWithDns(lookup).apply([_result("bb", Availability.TAKEN)])
        assert out[0].parked_hint == "domainrecover"

    def test_registry_parking_hint_wins_and_available_never_gets_one(self) -> None:
        parked = DnsEvidence(nameservers=("ns1.sedoparking.com",))
        lookup = FakeDnsLookup(default=parked)
        taken = DomainCheckResult(
            domain=hack("bb", "to"), availability=Availability.TAKEN, parked_hint="afternic"
        )
        out = ConfirmWithDns(lookup).apply([taken, _result("aa", Availability.AVAILABLE)])
        assert out[0].parked_hint == "afternic"
        assert out[1].parked_hint == ""


class TestDnsConfirmingWriter:
    def test_streams_in_arrival_order_and_counts_conflicts(self) -> None:
        inner = CollectingWriter()
        writer = DnsConfirmingWriter(
            inner, ConfirmWithDns(FakeDnsLookup({"aa.to": DELEGATED}), workers=3)
        )
        for sld, availability in [
            ("aa", Availability.AVAILABLE),
            ("bb", Availability.ERROR),
            ("cc", Availability.TAKEN),
        ]:
            writer.write_result(_result(sld, availability))
        writer.flush()
        assert [r.domain.sld for r in inner.results] == ["aa", "bb", "cc"]
        assert inner.results[0].dns_conflict
        assert inner.results[1].dns is None
        assert inner.results[2].dns == DnsEvidence()
        assert writer.conflicts == 1
        assert inner.flushed

    def test_writes_ready_results_before_flush(self) -> None:
        inner = CollectingWriter()
        writer = DnsConfirmingWriter(inner, ConfirmWithDns(FakeDnsLookup()))
        writer.write_result(_result("aa", Availability.ERROR))
        assert [r.domain.sld for r in inner.results] == ["aa"]
        writer.flush()

    def test_inner_writer_only_on_the_calling_thread(self) -> None:
        threads: set[str] = set()

        class ThreadRecorder(CollectingWriter):
            def write_result(self, result: DomainCheckResult) -> None:
                threads.add(threading.current_thread().name)
                super().write_result(result)

        writer = DnsConfirmingWriter(ThreadRecorder(), ConfirmWithDns(FakeDnsLookup()))
        for i in range(20):
            writer.write_result(_result(f"x{i}", Availability.AVAILABLE))
        writer.flush()
        assert threads == {threading.current_thread().name}

    def test_interrupted_flush_keeps_results_without_dns(self) -> None:
        inner = CollectingWriter()
        writer = DnsConfirmingWriter(inner, ConfirmWithDns(FakeDnsLookup()))
        writer.write_result(_result("aa", Availability.ERROR))
        stuck: Future[DomainCheckResult] = Future()
        original = _result("bb", Availability.AVAILABLE)
        writer._pending.append((original, stuck))
        with (
            patch.object(Future, "result", side_effect=KeyboardInterrupt),
            pytest.raises(KeyboardInterrupt),
        ):
            writer.flush()
        assert [r.domain.sld for r in inner.results] == ["aa", "bb"]
        assert inner.results[1] is original
        assert inner.flushed

    def test_cli_builds_the_real_lookup_by_default(self) -> None:
        args = build_parser().parse_args(["check", "--range-max", "1", "--confirm-dns"])
        inner = CollectingWriter()
        writer = _confirming_writer(args, inner, None)
        assert isinstance(writer, DnsConfirmingWriter)
        assert isinstance(writer._confirm._lookup, DnsPythonLookup)
        writer.flush()
        plain = build_parser().parse_args(["check", "--range-max", "1"])
        assert _confirming_writer(plain, inner, None) is inner


class TestConsole:
    def test_conflict_prints_a_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        conflict = ConfirmWithDns(FakeDnsLookup(default=DELEGATED)).confirm(
            _result("x", Availability.AVAILABLE, "io")
        )
        ConsoleResultWriter().write_result(conflict)
        assert capsys.readouterr().out == (
            "  AVAILABLE? x.io -- registry says free but DNS has NS records\n"
        )

    def test_confirmed_available_prints_as_before(self, capsys: pytest.CaptureFixture[str]) -> None:
        confirmed = ConfirmWithDns(FakeDnsLookup()).confirm(_result("x", Availability.AVAILABLE))
        ConsoleResultWriter().write_result(confirmed)
        assert capsys.readouterr().out == "  AVAILABLE: x.to (word: 'xto')\n"


def _check(*extra: str) -> list[str]:
    return ["check", "--range-max", "1", "--range-end", "c", "--no-progress", "--no-cache", *extra]


class TestCliConfirmDns:
    def test_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        registrar = ScriptedRegistrar(
            {"a.to": Availability.AVAILABLE, "b.to": Availability.AVAILABLE},
            default=Availability.TAKEN,
        )
        lookup = FakeDnsLookup({"b.to": DELEGATED})
        code = main(
            [*_check("--confirm-dns", "--show-taken")],
            catalog=FakeCatalog(registrar),
            dns_lookup=lookup,
        )
        assert code == EXIT_OK
        out, err = capsys.readouterr()
        assert out.splitlines() == [
            "  AVAILABLE: a.to (word: 'ato')",
            "  AVAILABLE? b.to -- registry says free but DNS has NS records",
            "  TAKEN:     c.to",
        ]
        assert sorted(lookup.calls) == ["a.to", "b.to", "c.to"]
        assert (
            "warning: 1 name the registry reported available has NS records in DNS "
            "(marked 'AVAILABLE?'); do not count on them being free" in err
        )

    def test_without_flag_no_lookups(self, capsys: pytest.CaptureFixture[str]) -> None:
        lookup = FakeDnsLookup(default=DELEGATED)
        registrar = ScriptedRegistrar(default=Availability.AVAILABLE)
        assert main(_check(), catalog=FakeCatalog(registrar), dns_lookup=lookup) == EXIT_OK
        assert lookup.calls == []
        assert "AVAILABLE?" not in capsys.readouterr().out

    def test_csv_output_gets_confirmed_results(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        output = tmp_path / "out.csv"
        registrar = ScriptedRegistrar(default=Availability.AVAILABLE)
        lookup = FakeDnsLookup()
        argv = _check("--confirm-dns", "--output", str(output))
        assert main(argv, catalog=FakeCatalog(registrar), dns_lookup=lookup) == EXIT_OK
        # The file writer sits behind the DNS writer (its dns columns are Agent B's).
        assert len(output.read_text().splitlines()) == 4
        assert sorted(lookup.calls) == ["a.to", "b.to", "c.to"]


class TestUnreachableRegistry:
    def test_all_errors_names_the_registry(self, capsys: pytest.CaptureFixture[str]) -> None:
        nic_ar = ScriptedRegistrar(default=Availability.ERROR)
        tonic = ScriptedRegistrar(default=Availability.AVAILABLE)
        catalog = FakeCatalog(by_tld={"com.ar": nic_ar, "to": tonic})
        argv = ["--tld", "to,com.ar", *_check()]
        assert main(argv, catalog=catalog) == EXIT_FAILURE
        err = capsys.readouterr().err
        assert "warning: registry for .com.ar did not respond (3 errors); re-run later" in err
        assert "registry for .to" not in err

    def test_partial_failures_are_named_too(self, capsys: pytest.CaptureFixture[str]) -> None:
        registrar = ScriptedRegistrar({"a": Availability.ERROR}, default=Availability.TAKEN)
        assert main(_check(), catalog=FakeCatalog(registrar)) == EXIT_FAILURE
        err = capsys.readouterr().err
        assert "warning: registry for .to failed 1 of 3 checks; re-run later for the rest" in err

    def test_summary_tallies_per_tld(self) -> None:
        summary = CheckSummary(
            errors=4,
            tlds=(
                TldTally("io", checked=5, errors=1),
                TldTally("to", checked=2),
                TldTally("com.ar", checked=3, errors=3),
            ),
        )
        assert [t.suffix for t in summary.tlds_with_errors] == ["com.ar", "io"]
        assert summary == CheckSummary(errors=4)  # the breakdown is not part of equality
        assert not TldTally("x").unreachable

    def test_interrupted_run_still_names_it(self, capsys: pytest.CaptureFixture[str]) -> None:
        registrar = ScriptedRegistrar({"a": Availability.ERROR, "b": KeyboardInterrupt})
        assert main(_check(), catalog=FakeCatalog(registrar)) == 130
        err = capsys.readouterr().err
        assert "registry for .to did not respond (1 error)" in err
