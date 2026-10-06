"""Label validation and IDN handling (audit H1): bad names never reach a registrar."""

import argparse
import sqlite3
import unicodedata
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import idna
import pytest

from domainhack.adapters.cached_registrar import CachedRegistrarClient
from domainhack.adapters.console_writer import ConsoleResultWriter
from domainhack.adapters.rdap_registrar import RdapRegistrarClient
from domainhack.adapters.whois_registrar import (
    UnsafeWhoisQueryError,
    WhoisRegistrarClient,
    check_query_name,
    whois_query,
)
from domainhack.cli.app import _progress_total, build_parser, cmd_check, cmd_filter
from domainhack.domain.entities import (
    TLD,
    Availability,
    DomainCheckResult,
    DomainHack,
    InvalidLabelError,
    to_ascii_label,
)
from domainhack.domain.label_rules import DEFAULT_LABEL_RULE, label_rule_for
from domainhack.ports.registrar import RegistrarClient
from domainhack.usecases.filter_words import FilterWordsUseCase
from domainhack.usecases.generate_range import RangeCandidatesUseCase, RangeWordSource
from tests.conftest import FakeWordSource

TO, IT, DE, IO = TLD("to"), TLD("it"), TLD("de"), TLD("io")

# Every malformed word from the audit, as it appears in a word list.
AUDIT_WORDS = [
    "pañito",  # IDN under .to, which has no IDN: was AVAILABLE
    "can'tto",  # apostrophe: was AVAILABLE
    "../../help?q=xto",  # path traversal / query injection into the RDAP URL
]
AUDIT_WHOIS_WORDS = [
    "foo bar -h xit",  # space + query flag
    "-t dn,ace xit",  # leading registry query flag
    "nul\x00x.it",  # NUL byte and an empty label
    "a\r\nbit",  # CR/LF injection
]


def _forge(domain: DomainHack, ascii_sld: str) -> DomainHack:
    """Bypass validation to exercise the adapters' own defences."""
    object.__setattr__(domain, "ascii_sld", ascii_sld)
    return domain


class TestLdhRules:
    @pytest.mark.parametrize(
        "sld",
        ["a", "pla", "a1", "1a", "a-b", "x" * 63, "123"],
    )
    def test_valid(self, sld: str) -> None:
        assert DomainHack.from_sld(sld, DE).fqdn == f"{sld}.de"

    @pytest.mark.parametrize(
        "sld",
        [
            "",
            "-ab",
            "ab-",
            "ab--cd",  # '--' in positions 3-4 without xn--
            "x" * 64,
            "a_b",
            "a.b",
            "a b",
            "can't",
            "a\rb",
            "a\nb",
            "a\x00b",
            "../../help?q=x",
            "a/b",
        ],
    )
    def test_invalid(self, sld: str) -> None:
        with pytest.raises(InvalidLabelError):
            DomainHack.from_sld(sld, DE)

    def test_constructor_validates_too(self) -> None:
        with pytest.raises(InvalidLabelError):
            DomainHack(word="a\r\nbto", sld="a\r\nb", tld=TO)

    def test_invalid_label_error_is_a_value_error(self) -> None:
        assert issubclass(InvalidLabelError, ValueError)

    def test_tld_must_be_ascii(self) -> None:
        with pytest.raises(ValueError):
            TLD("ñx")


class TestAuditExamples:
    @pytest.mark.parametrize("word", AUDIT_WORDS)
    def test_from_word_rejects(self, word: str) -> None:
        with pytest.raises(InvalidLabelError):
            DomainHack.from_word(word, TO)

    @pytest.mark.parametrize("word", AUDIT_WHOIS_WORDS)
    def test_from_word_rejects_whois_injection(self, word: str) -> None:
        with pytest.raises(InvalidLabelError):
            DomainHack.from_word(word, IT)

    def test_filter_skips_and_counts(self) -> None:
        words = ["plato", *AUDIT_WORDS, "hello"]
        uc = FilterWordsUseCase(FakeWordSource(words), TO)
        assert [h.fqdn for h in uc.execute()] == ["pla.to"]
        assert uc.skipped == len(AUDIT_WORDS)

    def test_filter_skips_whois_injection(self) -> None:
        uc = FilterWordsUseCase(FakeWordSource([*AUDIT_WHOIS_WORDS, "cabit"]), IT)
        assert [h.fqdn for h in uc.execute()] == ["cab.it"]
        assert uc.skipped == len(AUDIT_WHOIS_WORDS)

    def test_non_matching_words_are_not_counted_as_skipped(self) -> None:
        uc = FilterWordsUseCase(FakeWordSource(["hello", "can't"]), TO)
        assert list(uc.execute()) == []
        assert uc.skipped == 0


class TestIdn:
    def test_nandu_with_idn_tld(self) -> None:
        hack = DomainHack.from_sld("ñandú", DE)
        assert hack.sld == "ñandú"
        assert hack.ascii_sld == "xn--and-6ma2c"
        assert hack.fqdn == "xn--and-6ma2c.de"
        assert hack.display == "ñandú.de"
        assert hack.is_idn
        # Round trip: the A-label decodes back to what the user wrote.
        assert idna.decode(hack.fqdn) == hack.display

    def test_nandu_with_tld_without_idn(self) -> None:
        with pytest.raises(InvalidLabelError, match="does not accept IDN"):
            DomainHack.from_sld("ñandú", TO)
        with pytest.raises(InvalidLabelError, match="does not accept IDN"):
            DomainHack.from_sld("ñandú", IO)

    def test_filter_idn_round_trip(self) -> None:
        uc = FilterWordsUseCase(FakeWordSource(["ñandúde", "ñandúto"]), [DE, TO])
        hacks = list(uc.execute())
        assert [(h.display, h.fqdn) for h in hacks] == [("ñandú.de", "xn--and-6ma2c.de")]
        assert uc.skipped == 1

    def test_ascii_name_is_unchanged(self) -> None:
        hack = DomainHack.from_sld("pla", TO)
        assert hack.fqdn == hack.display == "pla.to"
        assert not hack.is_idn

    def test_nfc_normalization(self) -> None:
        decomposed = unicodedata.normalize("NFD", "ñandú")
        assert decomposed != "ñandú"
        assert DomainHack.from_sld(decomposed, DE) == DomainHack.from_sld("ñandú", DE)

    def test_uppercase_unicode_is_lowered(self) -> None:
        assert DomainHack.from_sld("ÑANDÚ", DE).fqdn == "xn--and-6ma2c.de"

    def test_ascii_a_label_input(self) -> None:
        hack = DomainHack.from_sld("xn--and-6ma2c", DE)
        assert hack.fqdn == "xn--and-6ma2c.de"

    def test_a_label_rejected_without_idn_support(self) -> None:
        with pytest.raises(InvalidLabelError):
            DomainHack.from_sld("xn--and-6ma2c", TO)

    @pytest.mark.parametrize("sld", ["xn--", "xn--zz", "xn--a-ecp"])
    def test_bogus_a_label(self, sld: str) -> None:
        with pytest.raises(InvalidLabelError):
            DomainHack.from_sld(sld, DE)

    @pytest.mark.parametrize("sld", ["a‍b", "☃x", "áb"])
    def test_disallowed_idna2008(self, sld: str) -> None:
        # ZWJ (CONTEXTJ), symbols and a leading combining mark are invalid.
        with pytest.raises(InvalidLabelError):
            DomainHack.from_sld(sld if sld != "áb" else "́ab", DE)


class TestTldRules:
    def test_unknown_tld_uses_default(self) -> None:
        assert label_rule_for("ing") == DEFAULT_LABEL_RULE
        assert label_rule_for(".IT") == label_rule_for("it")

    @pytest.mark.parametrize("sld", ["a", "ab"])
    def test_it_requires_three(self, sld: str) -> None:
        with pytest.raises(InvalidLabelError, match="at least 3"):
            DomainHack.from_sld(sld, IT)
        assert DomainHack.from_sld(sld, DE).fqdn == f"{sld}.de"

    def test_it_min_length_counts_unicode_characters(self) -> None:
        assert DomainHack.from_sld("abè", IT).fqdn.startswith("xn--")

    def test_it_forbids_xn_prefix_for_ascii_names(self) -> None:
        with pytest.raises(InvalidLabelError):
            DomainHack.from_sld("xnabc", IT)
        assert DomainHack.from_sld("xnabc", DE).fqdn == "xnabc.de"
        # The A-label of a genuine IDN is fine.
        assert DomainHack.from_sld("città", IT).fqdn == "xn--citt-3na.it"

    def test_to_max_length(self) -> None:
        assert DomainHack.from_sld("x" * 61, TO)
        with pytest.raises(InvalidLabelError):
            DomainHack.from_sld("x" * 62, TO)

    def test_to_ascii_label_function(self) -> None:
        assert to_ascii_label("ñandú", DE) == "xn--and-6ma2c"


class TestRangeMode:
    def test_it_range_two_yields_nothing(self) -> None:
        uc = RangeCandidatesUseCase(RangeWordSource(2), IT)
        assert list(uc.execute()) == []
        assert uc.skipped == 26 + 26 * 26
        assert uc.total() == 0

    @pytest.mark.parametrize(
        ("max_length", "end_at"),
        [(3, None), (3, "xna"), (3, "xnz"), (3, "xmz"), (3, "zzz"), (3, "ab"), (3, "xnb")],
    )
    def test_total_matches_execute(self, max_length: int, end_at: str | None) -> None:
        uc = RangeCandidatesUseCase(RangeWordSource(max_length, end_at=end_at), [IT, TO, DE])
        assert uc.total() == len(list(uc.execute()))

    def test_it_range_three_skips_xn_prefix(self) -> None:
        uc = RangeCandidatesUseCase(RangeWordSource(3), IT)
        fqdns = [d.fqdn for d in uc.execute()]
        assert len(fqdns) == 26**3 - 26
        assert "xna.it" not in fqdns
        assert uc.skipped == 26 + 26 * 26 + 26

    def test_progress_total_respects_rules(self) -> None:
        args = build_parser().parse_args(["--tld", "it,to", "check", "--range-max", "2"])
        assert _progress_total(args) == 26 + 26 * 26  # only .to

    def test_cli_sends_no_queries(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        args = build_parser().parse_args(
            ["--tld", "it", "check", "--range-max", "2", "--no-progress", "--no-cache"]
        )
        inner = MagicMock(spec=RegistrarClient)
        with patch("domainhack.cli.app.build_registrar_for", return_value=inner):
            cmd_check(args)
        inner.check_availability.assert_not_called()
        captured = capsys.readouterr()
        assert "skipped 702 invalid candidates" in captured.err
        assert "Checked 0 domains" in captured.out


class TestCliReporting:
    def test_filter_prints_skip_count(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        words = tmp_path / "w.txt"
        words.write_text("plato\npañito\ncan'tto\n", encoding="utf-8")
        cmd_filter(argparse.Namespace(tld=[TO], file=words, min_length=0))
        captured = capsys.readouterr()
        assert captured.out.splitlines() == ["plato"]
        assert captured.err.splitlines() == ["skipped 2 invalid candidates"]

    def test_filter_prints_nothing_when_all_valid(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        words = tmp_path / "w.txt"
        words.write_text("plato\n", encoding="utf-8")
        cmd_filter(argparse.Namespace(tld=[TO], file=words, min_length=0))
        assert capsys.readouterr().err == ""

    def test_dry_run_prints_display_and_skip_count(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        words = tmp_path / "w.txt"
        words.write_text("ñandúde\npañito\n", encoding="utf-8")
        args = build_parser().parse_args(
            ["--tld", "de,to", "check", "--file", str(words), "--dry-run"]
        )
        cmd_check(args)
        captured = capsys.readouterr()
        assert "ñandú.de" in captured.out
        assert "pañi" not in captured.out
        assert captured.err.splitlines() == ["skipped 1 invalid candidates"]

    def test_check_queries_ascii_name(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        words = tmp_path / "w.txt"
        words.write_text("ñandúde\n", encoding="utf-8")
        args = build_parser().parse_args(
            ["--tld", "de", "check", "--file", str(words), "--no-progress", "--no-cache"]
        )
        inner = MagicMock(spec=RegistrarClient)
        inner.check_availability.side_effect = lambda d: DomainCheckResult(
            domain=d, availability=Availability.AVAILABLE
        )
        with patch("domainhack.cli.app.build_registrar_for", return_value=inner):
            cmd_check(args)
        (domain,) = [c.args[0] for c in inner.check_availability.call_args_list]
        assert domain.fqdn == "xn--and-6ma2c.de"
        assert "AVAILABLE: ñandú.de (xn--and-6ma2c.de)" in capsys.readouterr().out


class TestConsoleAndCache:
    def test_console_shows_display_and_a_label(self, capsys: pytest.CaptureFixture[str]) -> None:
        writer = ConsoleResultWriter(show_taken=True)
        hack = DomainHack.from_sld("ñandú", DE)
        writer.write_result(DomainCheckResult(domain=hack, availability=Availability.TAKEN))
        assert "TAKEN:     ñandú.de (xn--and-6ma2c.de)" in capsys.readouterr().out

    def test_cache_key_is_ascii(self, tmp_path: Path) -> None:
        inner = MagicMock(spec=RegistrarClient)
        inner.check_availability.side_effect = lambda d: DomainCheckResult(
            domain=d, availability=Availability.TAKEN
        )
        db = tmp_path / "c.sqlite3"
        with CachedRegistrarClient(inner, path=db) as cached:
            cached.check_availability(DomainHack.from_sld("ñandú", DE))
        with closing(sqlite3.connect(db)) as conn:
            rows = conn.execute("SELECT fqdn FROM results").fetchall()
        assert rows == [("xn--and-6ma2c.de",)]


class TestRdapDefence:
    def _client(self, seen: list[httpx.Request]) -> RdapRegistrarClient:
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(404)

        http = httpx.Client(transport=httpx.MockTransport(handler))
        return RdapRegistrarClient("https://rdap.example/rdap/", delay=0.0, client=http)

    def test_path_segment_is_quoted(self) -> None:
        seen: list[httpx.Request] = []
        forged = _forge(DomainHack.from_sld("x", TO), "../../help?q=x")
        self._client(seen).check_availability(forged)
        (request,) = seen
        assert request.url.path.startswith("/rdap/domain/")
        assert request.url.query == b""
        assert b"%2F" in request.url.raw_path and b"%3F" in request.url.raw_path

    def test_idn_is_queried_as_a_label(self) -> None:
        seen: list[httpx.Request] = []
        self._client(seen).check_availability(DomainHack.from_sld("ñandú", DE))
        assert seen[0].url.raw_path == b"/rdap/domain/xn--and-6ma2c.de"


class TestWhoisDefence:
    @pytest.mark.parametrize(
        "forged",
        ["foo bar -h x", "-t dn,ace x", "nul\x00x.", "a\r\nb", "a\nb", "a\rb", "ñ"],
    )
    def test_unsafe_names_are_error_without_connecting(self, forged: str) -> None:
        connector = MagicMock()
        client = WhoisRegistrarClient(delay=0.0, connect=connector)
        domain = _forge(DomainHack.from_sld("abc", IT), forged)
        result = client.check_availability(domain)
        assert result.availability is Availability.ERROR
        assert "Refused to send WHOIS query" in result.error_message
        connector.assert_not_called()

    @pytest.mark.parametrize("name", ["-h", "a b", "a\tb", "a\x00", "a\r", "a\n"])
    def test_check_query_name_rejects(self, name: str) -> None:
        with pytest.raises(UnsafeWhoisQueryError):
            check_query_name(name)

    def test_check_query_name_accepts_a_label(self) -> None:
        check_query_name("xn--and-6ma2c.de")

    @pytest.mark.parametrize("query", ["a\r\nb\r\n", "a\x00b\r\n", "\r\n", "a\nb"])
    def test_whois_query_rejects_multiline(self, query: str) -> None:
        connector = MagicMock()
        with pytest.raises(UnsafeWhoisQueryError):
            whois_query("whois.example", query, 1.0, connect=connector)
        connector.assert_not_called()
