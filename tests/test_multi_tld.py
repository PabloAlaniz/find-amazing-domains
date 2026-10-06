"""Phase 2: multi-TLD support (--tld to,io,in) and TLD-based registrar routing."""

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from domainhack.adapters._circuit import HostCircuitBreaker
from domainhack.cli.app import (
    _build_domains,
    _progress_total,
    _registrar_factory,
    build_parser,
    cmd_check,
    cmd_filter,
    main,
    parse_tld_list,
)
from domainhack.domain.entities import TLD, Availability
from domainhack.usecases.filter_words import FilterWordsUseCase
from tests.fakes import CatalogCall, FakeCatalog, FakeWordSource, ScriptedRegistrar, run_cli

TO, IO, IN = TLD("to"), TLD("io"), TLD("in")


class TestParseTldList:
    def test_single(self) -> None:
        assert parse_tld_list("to") == [TO]

    def test_multiple_preserves_order(self) -> None:
        assert parse_tld_list("to,io,in") == [TO, IO, IN]

    def test_dedupes_preserving_first_occurrence(self) -> None:
        assert parse_tld_list("io,to,io,TO") == [IO, TO]

    def test_normalizes_whitespace_case_and_dots(self) -> None:
        assert parse_tld_list(" .TO , io ,") == [TO, IO]

    @pytest.mark.parametrize(
        "value", ["t", "to,x", "to,i0", "", ",,", "co..uk", "comm.ar", "a.b.ar"]
    )
    def test_rejects_invalid(self, value: str) -> None:
        with pytest.raises(argparse.ArgumentTypeError):
            parse_tld_list(value)

    def test_parser_default_is_to(self) -> None:
        args = build_parser().parse_args(["check", "--range-max", "1"])
        assert args.tld == [TO]

    def test_parser_accepts_list(self) -> None:
        args = build_parser().parse_args(["--tld", "to,io", "check", "--range-max", "1"])
        assert args.tld == [TO, IO]

    def test_parser_error_on_invalid(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            build_parser().parse_args(["--tld", "to,x1", "check", "--range-max", "1"])
        assert exc_info.value.code == 2
        assert "invalid TLD: 'x1'" in capsys.readouterr().err


class TestFilterMultiTld:
    def test_word_matching_several_tlds_yields_each(self) -> None:
        source = FakeWordSource(["testing", "plato", "radio", "hello"])
        hacks = list(FilterWordsUseCase(source, [TLD("ng"), TLD("ing"), TO, IO]).execute())
        assert [h.fqdn for h in hacks] == ["testi.ng", "test.ing", "pla.to", "rad.io"]

    def test_single_tld_still_accepted(self) -> None:
        hacks = list(FilterWordsUseCase(FakeWordSource(["plato"]), TO).execute())
        assert [h.fqdn for h in hacks] == ["pla.to"]

    def test_min_length_applies_across_tlds(self) -> None:
        source = FakeWordSource(["ato", "radio"])
        hacks = list(FilterWordsUseCase(source, [TO, IO], min_length=4).execute())
        assert [h.fqdn for h in hacks] == ["rad.io"]


class TestCmdFilterOutput:
    def _args(self, tmp_path: Path, tlds: str) -> argparse.Namespace:
        words = tmp_path / "w.txt"
        words.write_text("plato\nradio\nberlin\nhello\n", encoding="utf-8")
        return argparse.Namespace(tld=parse_tld_list(tlds), file=words, min_length=0)

    def test_single_tld_prints_words(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cmd_filter(self._args(tmp_path, "to"))
        assert capsys.readouterr().out.splitlines() == ["plato"]

    def test_multi_tld_prints_word_and_fqdn(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cmd_filter(self._args(tmp_path, "to,io,in"))
        assert capsys.readouterr().out.splitlines() == [
            "plato -> pla.to",
            "radio -> rad.io",
            "berlin -> berl.in",
        ]

    def test_multi_tld_with_one_match_still_uses_multi_format(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cmd_filter(self._args(tmp_path, "to,ly"))
        assert capsys.readouterr().out.splitlines() == ["plato -> pla.to"]


class TestBuildDomainsMultiTld:
    def test_list_mode_every_word_tld_match(self, tmp_path: Path) -> None:
        words = tmp_path / "w.txt"
        words.write_text("plato\nradio\nhello\n", encoding="utf-8")
        args = argparse.Namespace(file=words, range_max=None, range_end=None)
        assert [d.fqdn for d in _build_domains(args, [TO, IO])] == ["pla.to", "rad.io"]

    def test_range_mode_is_sld_major(self) -> None:
        args = argparse.Namespace(file=None, range_max=1, range_end="b")
        assert [d.fqdn for d in _build_domains(args, [TO, IO])] == [
            "a.to",
            "a.io",
            "b.to",
            "b.io",
        ]


class TestProgressTotalMultiTld:
    def test_range_mode_multiplies_by_tld_count(self) -> None:
        args = build_parser().parse_args(["--tld", "to,io,in", "check", "--range-max", "2"])
        assert _progress_total(args) == (26 + 26 * 26) * 3

    def test_explicit_tlds_override(self) -> None:
        args = build_parser().parse_args(["--tld", "to,io,in", "check", "--range-max", "1"])
        assert _progress_total(args, [TO]) == 26

    def test_file_mode_is_indeterminate(self) -> None:
        args = build_parser().parse_args(["--tld", "to,io", "check", "--file", "w.txt"])
        assert _progress_total(args) is None


class TestRegistrarFactory:
    def test_delegates_to_catalog_with_delay(self) -> None:
        sentinel = ScriptedRegistrar()
        catalog = FakeCatalog(sentinel)
        client = _registrar_factory(argparse.Namespace(delay=2.5), catalog)(IN)
        assert client is sentinel
        (call,) = catalog.calls
        assert call == CatalogCall(IN, delay=2.5, breaker=call.breaker, contact=None)
        assert isinstance(call.breaker, HostCircuitBreaker)

    def test_passes_contact_to_catalog(self) -> None:
        catalog = FakeCatalog()
        _registrar_factory(argparse.Namespace(delay=0.0, contact="me@example.com"), catalog)(IN)
        assert catalog.calls[0].contact == "me@example.com"

    def test_one_breaker_is_shared_across_tlds(self) -> None:
        catalog = FakeCatalog()
        factory = _registrar_factory(argparse.Namespace(delay=0.0), catalog)
        factory(IN)
        factory(IO)
        first, second = (c.breaker for c in catalog.calls)
        assert first is second

    def test_unsupported_tld_is_none(self) -> None:
        assert _registrar_factory(argparse.Namespace(delay=0.0), FakeCatalog(None))(IO) is None

    def test_defaults_to_the_real_catalog(self) -> None:
        # .to resolves from the built-in RDAP overrides: no network involved.
        client = _registrar_factory(argparse.Namespace(delay=0.0))(TO)
        assert client is not None
        client.close()


def _fake_tonic() -> ScriptedRegistrar:
    return ScriptedRegistrar(default=Availability.AVAILABLE)


def _only_to(client: ScriptedRegistrar) -> FakeCatalog:
    """A catalog where only .to is supported, served by ``client``."""
    return FakeCatalog(by_tld={"to": client})


def _check_args(tlds: str, **kwargs: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "tld": parse_tld_list(tlds),
        "file": None,
        "range_max": 1,
        "range_end": "b",
        "dry_run": False,
        "delay": 0.0,
        "show_taken": True,
        "no_progress": True,
        "no_cache": True,
    }
    base.update(kwargs)
    return argparse.Namespace(**base)


class TestCmdCheckMultiTld:
    def test_dry_run_prints_all_tlds(self, capsys: pytest.CaptureFixture[str]) -> None:
        catalog = FakeCatalog()
        cmd_check(_check_args("to,in", dry_run=True), catalog=catalog)
        assert catalog.calls == []
        out = capsys.readouterr().out
        assert [line.split()[0] for line in out.splitlines()] == ["a.to", "a.in", "b.to", "b.in"]

    def test_in_domains_never_reach_tonic(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Regression: .in domains used to be sent to tonic.to's .to-only form."""
        tonic = _fake_tonic()
        cmd_check(_check_args("to,in"), catalog=_only_to(tonic))
        assert tonic.calls == ["a.to", "b.to"]
        captured = capsys.readouterr()
        assert "warning: no registrar supports .in; skipping" in captured.err
        assert ".in" not in captured.out
        assert tonic.closed

    def test_progress_total_uses_supported_tlds(self) -> None:
        with patch("domainhack.cli.app.CheckDomainsUseCase") as uc_cls:
            cmd_check(_check_args("to,in,io"), catalog=_only_to(_fake_tonic()))
        assert uc_cls.return_value.execute.call_args.kwargs["total"] == 2

    def test_all_unsupported_is_runtime_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        argv = ["--tld", "in,io", "check", "--range-max", "1", "--no-progress"]
        with patch("domainhack.cli.app.CheckDomainsUseCase") as uc_cls:
            assert main(argv, catalog=FakeCatalog(None)) == 1
        err = capsys.readouterr().err
        assert err == "error: no registrar supports .in, .io\n"
        uc_cls.assert_not_called()

    def test_router_is_wrapped_by_cache(self, tmp_path: Path) -> None:
        args = _check_args("to,in", no_cache=False, cache_path=tmp_path / "c.sqlite3")
        first, second = _fake_tonic(), _fake_tonic()
        cmd_check(args, catalog=_only_to(first))
        cmd_check(args, catalog=_only_to(second))
        assert first.calls == ["a.to", "b.to"]
        assert second.calls == []  # the second run is served from the cache


class TestCliSubprocess:
    def test_dry_run_multi_tld(self) -> None:
        result = run_cli("--tld", "to,io", "check", "--range-max", "1", "--dry-run")
        assert result.returncode == 0
        lines = result.stdout.strip().splitlines()
        assert len(lines) == 52
        assert "a.to" in lines[0]
        assert "a.io" in lines[1]
        assert "z.io" in lines[-1]

    def test_invalid_tld_is_usage_error(self) -> None:
        result = run_cli("--tld", "to,1", "filter", "x.txt")
        assert result.returncode == 2
        assert "invalid TLD" in result.stderr
