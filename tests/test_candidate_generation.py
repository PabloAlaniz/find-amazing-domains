"""Candidate generation end to end: pattern mode, frequency order, your own
prefixes and suffixes, and subdomain hacks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from domainhack.cli.app import EXIT_OK, EXIT_USAGE, _order, _progress_total, build_parser, main
from domainhack.cli.name_cmd import NameServices, NameUsageError, find_name, parse_affixes
from domainhack.domain.entities import TLD, Availability, DomainHack
from domainhack.mcp.server import build_server, run_options
from domainhack.usecases.brand_candidates import (
    MAX_AFFIXES,
    VARIANT_PREFIXES,
    VARIANT_SUFFIXES,
    BrandCandidates,
    CandidateKind,
    variant_affixes,
)
from domainhack.usecases.brand_report import TIER_HACK_NAME, build_brand_report, score
from domainhack.usecases.rank_candidates import (
    CandidateOrder,
    RankCandidatesUseCase,
    frequency_score,
    word_ranks,
)
from tests.fakes import FakeCatalog, FakeKnownTlds, ScriptedRegistrar

TO, IO, IT, COM, US = TLD("to"), TLD("io"), TLD("it"), TLD("com"), TLD("us")
KNOWN = FakeKnownTlds(["com", "io", "to", "us", "it", "ar", "com.ar"])


def _check(*argv: str, registrar: ScriptedRegistrar | None = None, tld: str = "to,io") -> int:
    base = ["--no-progress", "--no-cache"]
    return main(["--tld", tld, "check", *argv, *base], catalog=FakeCatalog(registrar))


def _name(*argv: str, script: dict[str, Any] | None = None) -> int:
    registrar = ScriptedRegistrar(script or {}, default=Availability.AVAILABLE)
    return main(
        ["name", *argv, "--no-progress", "--no-cache"],
        catalog=FakeCatalog(registrar),
        services=NameServices(KNOWN, None, supported_tlds=lambda: ()),
    )


def hack(word: str) -> DomainHack:
    domain = DomainHack.from_word(word, TO)
    assert domain is not None
    return domain


def _first_column(out: str) -> list[str]:
    return [line.split()[0] for line in out.splitlines()]


# ── check --pattern ──────────────────────────────────────


class TestPatternMode:
    def test_checks_every_match_under_every_tld(self) -> None:
        registrar = ScriptedRegistrar()
        assert _check("--pattern", "goV", registrar=registrar) == EXIT_OK
        assert registrar.calls[:4] == ["goa.to", "goa.io", "goe.to", "goe.io"]
        assert len(registrar.calls) == 10

    def test_dry_run_lists_what_a_run_checks(self, capsys: pytest.CaptureFixture[str]) -> None:
        argv = ("--pattern", "CV", "--limit", "3")
        assert _check(*argv, "--dry-run") == EXIT_OK
        listed = _first_column(capsys.readouterr().out)
        registrar = ScriptedRegistrar()
        _check(*argv, registrar=registrar)
        assert listed == registrar.calls == ["ba.to", "ba.io", "be.to", "be.io", "bi.to", "bi.io"]

    def test_names_a_tld_refuses_are_skipped(self, capsys: pytest.CaptureFixture[str]) -> None:
        # .it wants at least 3 characters.
        assert _check("--pattern", "aV", "--dry-run", tld="it,to") == EXIT_OK
        out, err = capsys.readouterr()
        assert _first_column(out) == ["aa.to", "ae.to", "ai.to", "ao.to", "au.to"]
        assert "skipped 5 invalid candidates" in err

    def test_progress_total_is_exact(self) -> None:
        args = build_parser().parse_args(["--tld", "to,it", "check", "--pattern", "CVC"])
        assert _progress_total(args) == 2 * 21 * 5 * 21
        args = build_parser().parse_args(["--tld", "to,it", "check", "--pattern", "CV"])
        assert _progress_total(args) == 105  # .it refuses 2-character names
        args.limit = 10
        assert _progress_total(args) == 10

    def test_order_is_as_generated(self) -> None:
        args = build_parser().parse_args(["check", "--pattern", "CV"])
        assert _order(args) is CandidateOrder.INPUT

    @pytest.mark.parametrize("order", ["score", "alpha", "frequency"])
    def test_sorting_is_refused(self, order: str, capsys: pytest.CaptureFixture[str]) -> None:
        assert _check("--pattern", "CV", "--order", order, "--dry-run") == EXIT_USAGE
        assert "range and pattern modes" in capsys.readouterr().err

    @pytest.mark.parametrize("pattern", ["", "cvX", "CV-", "C--V", "C V"])
    def test_bad_pattern_is_a_usage_error(
        self, pattern: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _check("--pattern", pattern, "--dry-run") == EXIT_USAGE
        assert "invalid pattern" in capsys.readouterr().err

    def test_one_source_at_a_time(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _check("--pattern", "CV", "--range-max", "2") == EXIT_USAGE
        assert _check("--pattern", "CV", "--range-end", "b") == EXIT_USAGE
        assert "--range-end requires --range-max" in capsys.readouterr().err

    def test_large_run_needs_yes(self, capsys: pytest.CaptureFixture[str]) -> None:
        registrar = ScriptedRegistrar()
        assert _check("--pattern", "CVCV", registrar=registrar) == EXIT_USAGE
        err = capsys.readouterr().err
        assert "Estimated 22,050 queries" in err
        assert "refusing to send more than 10,000 queries without --yes" in err
        assert registrar.calls == []

    def test_small_run_prints_the_estimate_and_goes_ahead(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _check("--pattern", "goV", registrar=ScriptedRegistrar()) == EXIT_OK
        assert "Estimated 10 queries" in capsys.readouterr().err


# ── check --order frequency ──────────────────────────────


@pytest.fixture
def lists(tmp_path: Path) -> tuple[Path, Path, Path]:
    words = tmp_path / "words.txt"
    words.write_text("monito\nplato\ngato\nradio\nesto\naudio\n", encoding="utf-8")
    en = tmp_path / "en.txt"
    en.write_text("the\nRadio\naudio\nradio\n", encoding="utf-8")
    es = tmp_path / "es.txt"
    es.write_text("esto\ngato\nplato\n", encoding="utf-8")
    return words, en, es


class TestFrequencyScore:
    def test_word_ranks_keep_the_first_position(self) -> None:
        assert word_ranks(["The", " of ", "the", "and"]) == {"the": 0, "of": 1, "and": 2}

    def test_common_words_first_then_the_rest_by_length(self) -> None:
        scorer = frequency_score({"plato": 0, "monito": 1})
        candidates = [hack("gato"), hack("monito"), hack("esto"), hack("plato")]
        ranked = RankCandidatesUseCase(CandidateOrder.FREQUENCY, scorer=scorer).execute(candidates)
        assert [d.fqdn for d in ranked] == ["pla.to", "moni.to", "es.to", "ga.to"]

    def test_limit_applies_after_ordering(self) -> None:
        scorer = frequency_score({"monito": 0})
        ranker = RankCandidatesUseCase(CandidateOrder.FREQUENCY, limit=1, scorer=scorer)
        assert ranker.materializes
        assert [d.fqdn for d in ranker.execute([hack("gato"), hack("monito")])] == ["moni.to"]


class TestFrequencyOrder:
    def test_most_common_first_across_lists(self, lists: tuple[Path, Path, Path]) -> None:
        words, en, es = lists
        registrar = ScriptedRegistrar()
        argv = ("--file", str(words), "--frequency-list", str(en), "--frequency-list", str(es))
        assert _check(*argv, "--order", "frequency", registrar=registrar) == EXIT_OK
        # esto is rank 0 in es; gato and radio are rank 1; audio and plato rank 2; monito unknown.
        assert registrar.calls == ["es.to", "ga.to", "rad.io", "aud.io", "pla.to", "moni.to"]

    def test_a_frequency_list_implies_the_order(self, lists: tuple[Path, Path, Path]) -> None:
        words, en, _ = lists
        args = build_parser().parse_args(
            ["check", "--file", str(words), "--frequency-list", str(en)]
        )
        assert _order(args) is CandidateOrder.FREQUENCY
        args = build_parser().parse_args(
            ["check", "--file", str(words), "--frequency-list", str(en), "--order", "alpha"]
        )
        assert _order(args) is CandidateOrder.ALPHA

    def test_the_order_needs_a_list(
        self, lists: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _check("--file", str(lists[0]), "--order", "frequency") == EXIT_USAGE
        assert "--order frequency needs --frequency-list" in capsys.readouterr().err

    def test_unreadable_list_fails_before_any_query(
        self, lists: tuple[Path, Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        registrar = ScriptedRegistrar()
        argv = ("--file", str(lists[0]), "--frequency-list", str(tmp_path / "nope.txt"))
        assert _check(*argv, registrar=registrar) == 1
        assert "cannot read word list" in capsys.readouterr().err
        assert registrar.calls == []


# ── name --prefix / --suffix ─────────────────────────────


class TestVariantAffixes:
    def test_nothing_unless_asked(self) -> None:
        assert variant_affixes(False) == ((), ())

    def test_variants_means_the_defaults(self) -> None:
        assert variant_affixes(True) == (VARIANT_PREFIXES, VARIANT_SUFFIXES)

    def test_a_custom_list_stands_alone(self) -> None:
        assert variant_affixes(False, ["Get", " try ", "get", ""]) == (("get", "try"), ())
        assert variant_affixes(False, None, ["hq"]) == ((), ("hq",))

    def test_variants_fills_the_other_side_with_its_default(self) -> None:
        assert variant_affixes(True, ["go"]) == (("go",), VARIANT_SUFFIXES)
        assert variant_affixes(True, None, ["hq"]) == (VARIANT_PREFIXES, ("hq",))

    def test_an_empty_custom_list_turns_that_side_off(self) -> None:
        assert variant_affixes(True, [], ["hq"]) == ((), ("hq",))

    def test_too_many_are_refused(self) -> None:
        many = [f"w{i}" for i in range(MAX_AFFIXES + 1)]
        with pytest.raises(ValueError, match="at most 10"):
            variant_affixes(False, many)
        with pytest.raises(ValueError, match="at most 10"):
            BrandCandidates("acme", [COM], KNOWN, suffixes=many)


class TestParseAffixes:
    def test_cleans_and_deduplicates(self) -> None:
        assert parse_affixes("Get, try,get,,my-own") == ["get", "try", "my-own"]

    @pytest.mark.parametrize(
        "value", ["", " , ", "a b", "ñu", "-a", "a-", "a.b", ",".join("abcdefghijk")]
    )
    def test_refuses(self, value: str) -> None:
        with pytest.raises(ValueError):
            parse_affixes(value)


class TestCustomVariants:
    def test_candidates(self) -> None:
        gen = BrandCandidates("acme", [IO, COM], KNOWN, prefixes=["go"], suffixes=["hq", "labs"])
        assert gen.variants_requested
        variants = [c for c in gen.execute() if c.kind is CandidateKind.VARIANT]
        assert [c.domain.fqdn for c in variants] == [
            "goacme.com",
            "acmehq.com",
            "acmelabs.com",
            "goacme.io",
            "acmehq.io",
            "acmelabs.io",
        ]
        assert variants[0].label == "go acme"

    def test_not_requested_by_default(self) -> None:
        assert not BrandCandidates("acme", [COM], KNOWN).variants_requested

    def test_cli(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = _name(
            "acme", "--tlds", "io", "--prefix", "go,try", "--suffix", "hq", "--format", "json"
        )
        assert code == EXIT_OK
        data = json.loads(capsys.readouterr().out)
        assert data["variants_requested"] is True
        assert {v["domain"] for v in data["variants"]} == {
            "goacme.com",
            "tryacme.com",
            "acmehq.com",
            "goacme.io",
            "tryacme.io",
            "acmehq.io",
        }

    def test_cli_prefix_alone_adds_no_suffixes(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _name("acme", "--tlds", "com", "--prefix", "go", "--format", "json") == EXIT_OK
        data = json.loads(capsys.readouterr().out)
        assert [v["domain"] for v in data["variants"]] == ["goacme.com"]

    def test_cli_with_variants_keeps_the_other_default(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = ("acme", "--tlds", "com", "--variants", "--prefix", "go", "--format", "json")
        assert _name(*argv) == EXIT_OK
        domains = {v["domain"] for v in json.loads(capsys.readouterr().out)["variants"]}
        assert domains == {"goacme.com", *(f"acme{s}.com" for s in VARIANT_SUFFIXES)}

    @pytest.mark.parametrize("value", ["", "a b", ",".join("abcdefghijk")])
    def test_cli_refuses_bad_lists(self, value: str, capsys: pytest.CaptureFixture[str]) -> None:
        assert _name("acme", "--prefix", value) == EXIT_USAGE
        assert "argument --prefix" in capsys.readouterr().err

    def test_config_file_supplies_presets_and_words(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        config = tmp_path / "c.toml"
        config.write_text(
            '[presets]\nmine = ["io", "com"]\n[name]\ntlds = "mine"\nsuffixes = ["hq"]\n'
        )
        assert _name("acme", "--config", str(config), "--format", "json") == EXIT_OK
        data = json.loads(capsys.readouterr().out)
        assert data["tlds"] == ["io", "com"]
        assert [v["domain"] for v in data["variants"]] == ["acmehq.com", "acmehq.io"]
        # The command line replaces the file's words.
        argv = ("acme", "--config", str(config), "--suffix", "labs", "--format", "json")
        assert _name(*argv) == EXIT_OK
        data = json.loads(capsys.readouterr().out)
        assert [v["domain"] for v in data["variants"]] == ["acmelabs.com", "acmelabs.io"]


# ── subdomain hacks ──────────────────────────────────────


class TestSubdomainHacks:
    def test_delicious(self) -> None:
        gen = BrandCandidates("delicious", [COM], KNOWN, subdomain_hacks=True)
        hacks = [c for c in gen.execute() if c.kind is CandidateKind.HACK]
        assert [(c.domain.fqdn, c.label) for c in hacks] == [
            ("delicio.us", "delicious"),
            ("licio.us", "de.licio.us"),
            ("icio.us", "del.icio.us"),
            ("cio.us", "deli.cio.us"),
        ]
        assert hacks[2].meaning == "with the subdomain 'del'"

    def test_off_by_default_in_the_use_case(self) -> None:
        gen = BrandCandidates("delicious", [COM], KNOWN)
        assert [c.domain.fqdn for c in gen.execute() if c.kind is CandidateKind.HACK] == [
            "delicio.us"
        ]

    def test_short_names_have_none(self) -> None:
        gen = BrandCandidates("plato", [COM], KNOWN, subdomain_hacks=True)
        assert [c.domain.fqdn for c in gen.execute() if c.kind is CandidateKind.HACK] == ["pla.to"]

    def test_nothing_when_the_name_ends_in_no_tld(self) -> None:
        gen = BrandCandidates("sumanda", [COM], KNOWN, subdomain_hacks=True)
        assert [c for c in gen.execute() if c.kind is CandidateKind.HACK] == []

    def test_multi_label_suffix(self) -> None:
        gen = BrandCandidates("superfotocomar", [COM], KNOWN, subdomain_hacks=True)
        hacks = [c for c in gen.execute() if c.kind is CandidateKind.HACK]
        assert "super.foto.com.ar" in [c.label for c in hacks]
        # "superfoto" + com.ar: com.ar is a public suffix, never a name to register.
        assert "com.ar" not in [c.domain.fqdn for c in hacks]

    def test_the_plain_hack_ranks_above_its_subdomain_forms(self) -> None:
        gen = BrandCandidates("delicious", [COM], KNOWN, subdomain_hacks=True)
        candidates = gen.execute()
        results = ScriptedRegistrar(default=Availability.AVAILABLE)
        report = build_brand_report(
            gen.name,
            candidates,
            [results.check_availability(c.domain) for c in candidates],
            tlds=[COM],
            suffix_hack_found=gen.suffix_hack_found,
            variants_requested=False,
        )
        names = [r.entry.name for r in report.recommendations]
        assert names[:2] == ["delicious.com", "delicio.us"]
        by_name = {r.entry.name: r for r in report.recommendations}
        assert by_name["icio.us"].reason == "domain hack (del.icio.us)"
        assert score(by_name["icio.us"].entry) < TIER_HACK_NAME * 100 - len("delicio.us")

    def test_cli_checks_them_by_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert (
            _name("delicious", "--tlds", "com", script={"icio.us": Availability.TAKEN}) == EXIT_OK
        )
        out = capsys.readouterr().out
        line = next(ln for ln in out.splitlines() if ln.startswith("  icio.us"))
        assert " ".join(line.split()) == ("icio.us taken del.icio.us: with the subdomain 'del'")

    def test_cli_can_turn_them_off(self, capsys: pytest.CaptureFixture[str]) -> None:
        argv = ("delicious", "--tlds", "com", "--no-subdomain-hacks", "--format", "json")
        assert _name(*argv) == EXIT_OK
        domains = [h["domain"] for h in json.loads(capsys.readouterr().out)["hacks"]]
        assert "delicio.us" in domains
        assert "icio.us" not in domains


# ── MCP ──────────────────────────────────────────────────


def _tool(server: Any, **arguments: Any) -> Any:
    async def go() -> Any:
        return await server.call_tool("find_domain_for_name", arguments)

    return anyio.run(go)


def _server(run: Any = None) -> Any:
    return build_server(
        run=run if run is not None else run_options(["--no-cache"]),
        catalog=FakeCatalog(ScriptedRegistrar(default=Availability.AVAILABLE)),
        services=NameServices(KNOWN, None, supported_tlds=lambda: ()),
    )


class TestMcp:
    def test_prefixes_suffixes_and_subdomain_hacks(self) -> None:
        result = _tool(_server(), name="delicious", tlds="com", prefixes=["Go"], suffixes=["hq"])
        assert not result.is_error, result.content
        data = result.structured_content
        assert [v["domain"] for v in data["variants"]] == ["godelicious.com", "delicioushq.com"]
        assert "icio.us" in [h["domain"] for h in data["hacks"]]

    def test_subdomain_hacks_can_be_turned_off(self) -> None:
        result = _tool(_server(), name="delicious", tlds="com", subdomain_hacks=False)
        domains = [h["domain"] for h in result.structured_content["hacks"]]
        assert "delicio.us" in domains
        assert "icio.us" not in domains

    def test_bad_words_are_a_tool_error(self) -> None:
        with pytest.raises(ToolError, match="not letters, digits and inner hyphens"):
            _tool(_server(), name="acme", prefixes=["a b"])

    def test_reads_the_configuration_file(self, tmp_path: Path) -> None:
        config = tmp_path / "c.toml"
        config.write_text(
            '[run]\ndelay = 3\n[presets]\nmine = ["io"]\n[name]\ntlds = "mine"\nsuffixes = ["hq"]\n'
        )
        run = run_options(["--no-cache", "--config", str(config)])
        assert run.delay == 3.0
        server = _server(run)
        data = _tool(server, name="acme").structured_content
        assert data["tlds"] == ["io"]
        assert [v["domain"] for v in data["variants"]] == ["acmehq.com", "acmehq.io"]
        # Naming words in the call replaces the file's.
        data = _tool(server, name="acme", prefixes=["go"]).structured_content
        assert [v["domain"] for v in data["variants"]] == ["goacme.com", "goacme.io"]
        assert run_options(["--no-config", "--config", str(config)]).delay == 1.0

    def test_a_bad_configuration_file_stops_the_server(self, tmp_path: Path) -> None:
        config = tmp_path / "c.toml"
        config.write_text("[nope]\n")
        with pytest.raises(SystemExit) as info:
            run_options(["--config", str(config)])
        assert info.value.code == 2


def test_too_many_words_given_to_find_name_are_a_usage_error() -> None:
    many = [f"w{i}" for i in range(MAX_AFFIXES + 1)]
    with pytest.raises(NameUsageError, match="at most 10"):
        find_name(
            "acme",
            "com",
            run=run_options(["--no-cache"]),
            suffixes=many,
            catalog=FakeCatalog(ScriptedRegistrar()),
            services=NameServices(KNOWN, None, supported_tlds=lambda: ()),
        )


def test_domainhack_word_of_a_pattern_name() -> None:
    assert DomainHack.from_sld("baba", TO).word == "babato"
