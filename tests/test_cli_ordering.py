"""--order, --limit and the brute-force guardrail."""

import argparse
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from domainhack.adapters.pacing import pacing_for
from domainhack.adapters.rdap_registrar import RdapRegistrarClient
from domainhack.adapters.whois_registrar import WhoisRegistrarClient
from domainhack.cli.app import _order, _progress_total, build_parser, main
from domainhack.domain.entities import TLD
from domainhack.ports.registrar import RegistrarClient
from domainhack.usecases.estimate_run import (
    GUARDRAIL_MAX_QUERIES,
    HostLoad,
    Pacing,
    RunEstimate,
    estimate_run,
    format_duration,
)
from domainhack.usecases.rank_candidates import CandidateOrder
from tests.fakes import FakeCatalog, ScriptedRegistrar

TO, IO, SH, IT = TLD("to"), TLD("io"), TLD("sh"), TLD("it")
WORDS = ["monito", "plato", "gato", "radio", "esto", "audio"]


@pytest.fixture
def words(tmp_path: Path) -> Path:
    path = tmp_path / "words.txt"
    path.write_text("\n".join(WORDS) + "\n", encoding="utf-8")
    return path


def _check(*argv: str, registrar: ScriptedRegistrar | None = None) -> int:
    base = ["--no-progress", "--no-cache"]
    return main(["--tld", "to,io", "check", *argv, *base], catalog=FakeCatalog(registrar))


class TestOptions:
    def test_defaults(self) -> None:
        args = build_parser().parse_args(["check", "--file", "w.txt"])
        assert args.order is None
        assert args.limit is None
        assert args.yes is False
        assert _order(args) is CandidateOrder.SCORE

    def test_range_mode_defaults_to_input_order(self) -> None:
        args = build_parser().parse_args(["check", "--range-max", "1"])
        assert _order(args) is CandidateOrder.INPUT

    @pytest.mark.parametrize("value", ["0", "-1", "x"])
    def test_limit_must_be_a_positive_integer(
        self, value: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["check", "--range-max", "1", "--limit", value]) == 2
        assert "--limit" in capsys.readouterr().err

    def test_unknown_order_is_a_usage_error(self) -> None:
        assert main(["check", "--file", "w.txt", "--order", "random"]) == 2

    @pytest.mark.parametrize("order", ["score", "alpha"])
    def test_sorting_range_mode_is_a_usage_error(
        self, order: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["check", "--range-max", "1", "--order", order, "--dry-run"]) == 2
        assert "needs --file" in capsys.readouterr().err

    def test_range_mode_accepts_input_order(self) -> None:
        assert main(["check", "--range-max", "1", "--order", "input", "--dry-run"]) == 0


class TestOrderAndLimitInRuns:
    def test_score_order_by_default(self, words: Path) -> None:
        registrar = ScriptedRegistrar()
        assert _check("--file", str(words), registrar=registrar) == 0
        assert registrar.calls == ["es.to", "ga.to", "aud.io", "pla.to", "rad.io", "moni.to"]

    def test_alpha_order(self, words: Path) -> None:
        registrar = ScriptedRegistrar()
        _check("--file", str(words), "--order", "alpha", registrar=registrar)
        assert registrar.calls == ["aud.io", "es.to", "ga.to", "moni.to", "pla.to", "rad.io"]

    def test_input_order(self, words: Path) -> None:
        registrar = ScriptedRegistrar()
        _check("--file", str(words), "--order", "input", registrar=registrar)
        assert registrar.calls == ["moni.to", "pla.to", "ga.to", "rad.io", "es.to", "aud.io"]

    def test_limit_is_per_tld_after_ordering(self, words: Path) -> None:
        registrar = ScriptedRegistrar()
        _check("--file", str(words), "--limit", "2", registrar=registrar)
        assert registrar.calls == ["es.to", "ga.to", "aud.io", "rad.io"]

    def test_limit_in_range_mode(self) -> None:
        registrar = ScriptedRegistrar()
        _check("--range-max", "2", "--limit", "3", registrar=registrar)
        assert registrar.calls == ["a.to", "a.io", "b.to", "b.io", "c.to", "c.io"]

    @pytest.mark.parametrize("order", ["score", "alpha", "input"])
    def test_dry_run_shows_exactly_what_would_be_checked(
        self, words: Path, order: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = ("--file", str(words), "--order", order, "--limit", "2")
        assert _check(*argv, "--dry-run") == 0
        listed = [line.split()[0] for line in capsys.readouterr().out.splitlines()]
        registrar = ScriptedRegistrar()
        _check(*argv, registrar=registrar)
        assert listed == registrar.calls
        assert len(listed) == 4


class TestProgressTotal:
    @staticmethod
    def _total(*argv: str) -> int | None:
        with patch("domainhack.cli.app.CheckDomainsUseCase") as uc_cls:
            _check(*argv, registrar=ScriptedRegistrar())
        total: int | None = uc_cls.return_value.execute.call_args.kwargs["total"]
        return total

    def test_sorted_file_mode_total_is_exact(self, words: Path) -> None:
        assert self._total("--file", str(words)) == 6

    def test_sorted_file_mode_total_reflects_the_limit(self, words: Path) -> None:
        assert self._total("--file", str(words), "--limit", "1") == 2

    def test_input_order_file_mode_stays_indeterminate(self, words: Path) -> None:
        assert self._total("--file", str(words), "--order", "input", "--limit", "1") is None

    def test_range_mode_total_reflects_the_limit(self) -> None:
        assert self._total("--range-max", "2", "--limit", "30") == 60
        assert self._total("--range-max", "1", "--limit", "30") == 52  # fewer than the limit

    def test_range_progress_total_helper(self) -> None:
        args = build_parser().parse_args(["--tld", "to,io", "check", "--range-max", "3"])
        assert _progress_total(args) == 2 * (26 + 26**2 + 26**3)
        args.limit = 100
        assert _progress_total(args) == 200


class TestGuardrail:
    def test_small_run_prints_estimate_and_proceeds(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        registrar = ScriptedRegistrar()
        assert _check("--range-max", "1", "--delay", "2", registrar=registrar) == 0
        err = capsys.readouterr().err
        assert "Estimated 52 queries to 2 hosts (.to, .io): at least 52 s" in err
        assert len(registrar.calls) == 52

    def test_large_run_is_refused_without_yes(self, capsys: pytest.CaptureFixture[str]) -> None:
        registrar = ScriptedRegistrar()
        assert _check("--range-max", "3", registrar=registrar) == 2
        err = capsys.readouterr().err
        assert "Estimated 36,556 queries to 2 hosts" in err
        assert "error: refusing to send more than 10,000 queries without --yes" in err
        assert registrar.calls == []
        assert registrar.closed

    def test_yes_lets_a_large_run_proceed(self) -> None:
        with patch("domainhack.cli.app.CheckDomainsUseCase") as uc_cls:
            uc_cls.return_value.execute.return_value.errors = 0
            uc_cls.return_value.execute.return_value.interrupted = False
            code = _check("--range-max", "3", "--yes", registrar=ScriptedRegistrar())
        assert code == 0
        assert uc_cls.return_value.execute.call_args.kwargs["total"] == 36_556

    def test_limit_brings_a_run_under_the_threshold(self) -> None:
        registrar = ScriptedRegistrar()
        assert _check("--range-max", "3", "--limit", "5000", registrar=registrar) == 0
        assert len(registrar.calls) == 10_000

    def test_dry_run_is_exempt(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _check("--range-max", "3", "--dry-run") == 0
        captured = capsys.readouterr()
        assert len(captured.out.splitlines()) == 36_556
        assert captured.err == ""

    def test_file_mode_has_no_guardrail(
        self, words: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _check("--file", str(words), registrar=ScriptedRegistrar())
        assert "Estimated" not in capsys.readouterr().err


class TestEstimate:
    def test_threshold_boundary(self) -> None:
        at = RunEstimate((HostLoad("h", GUARDRAIL_MAX_QUERIES, 1.0),))
        over = RunEstimate((HostLoad("h", GUARDRAIL_MAX_QUERIES + 1, 1.0),))
        assert not at.exceeds()
        assert over.exceeds()

    def test_tlds_sharing_a_host_add_up_and_the_busiest_host_sets_the_time(self) -> None:
        hosts = {
            TO: Pacing("rdap.tonic", 1.0),
            IO: Pacing("rdap.identity", 1.0),
            SH: Pacing("rdap.identity", 1.0),
            IT: Pacing("whois.nic.it", 4.0),
        }
        estimate = estimate_run({TO: 100, IO: 100, SH: 100, IT: 60}, hosts.__getitem__)
        assert estimate.queries == 360
        assert [(h.host, h.queries) for h in estimate.hosts] == [
            ("rdap.tonic", 100),
            ("rdap.identity", 200),
            ("whois.nic.it", 60),
        ]
        assert estimate.seconds == 240.0  # whois.nic.it: 60 x 4 s

    def test_empty_estimate(self) -> None:
        assert RunEstimate(()).seconds == 0.0

    @pytest.mark.parametrize(
        ("seconds", "text"),
        [
            (45, "45 s"),
            (60, "1 min"),
            (3599, "59 min"),
            (18_252, "5 h 4 min"),
            (475_254, "5 d 12 h"),
        ],
    )
    def test_format_duration(self, seconds: float, text: str) -> None:
        assert format_duration(seconds) == text


class TestPacing:
    def test_rdap_client_is_paced_at_delay_on_its_host(self) -> None:
        http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
        client = RdapRegistrarClient("https://rdap.example.test/rdap/", client=http)
        assert pacing_for(client, IO, 1.5) == Pacing("rdap.example.test", 1.5)
        http.close()

    def test_whois_client_uses_the_server_floor(self) -> None:
        client = WhoisRegistrarClient()
        assert pacing_for(client, IT, 1.0) == Pacing("whois.nic.it", 4.0)
        assert pacing_for(client, IT, 9.0) == Pacing("whois.nic.it", 9.0)

    @pytest.mark.parametrize("client", [None, ScriptedRegistrar(), WhoisRegistrarClient()])
    def test_unknown_clients_are_their_own_host(self, client: RegistrarClient | None) -> None:
        assert pacing_for(client, TLD("zz"), 1.0) == Pacing(".zz", 1.0)


def test_namespace_without_new_options_keeps_old_behaviour() -> None:
    args = argparse.Namespace(file=None, range_max=1, range_end=None, tld="to")
    assert _order(args) is CandidateOrder.INPUT
    assert _progress_total(args) == 26
