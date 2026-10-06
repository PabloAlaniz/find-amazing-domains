"""Candidates for ``domainhack name``: exact names, hacks, variants, and --tlds presets."""

import json
from typing import ClassVar

import pytest

from domainhack.adapters.brand_data import (
    DEFAULT_PRESET,
    load_hack_words,
    load_tld_presets,
    parse_presets,
)
from domainhack.domain.entities import TLD
from domainhack.usecases.brand_candidates import (
    CATALOG_PRESET,
    VARIANT_PREFIXES,
    VARIANT_SUFFIXES,
    VARIANT_TLD_LIMIT,
    BrandCandidates,
    CandidateKind,
    HackWord,
    Presets,
    resolve_tld_spec,
)
from tests.fakes import FakeKnownTlds

KNOWN = FakeKnownTlds(["com", "app", "io", "ai", "co", "dev", "so", "xyz", "to", "mo", "it"])
STARTUP = [TLD(s) for s in ("com", "app", "io", "ai", "co", "dev", "so", "xyz")]
WORDS = (HackWord("studio", "a studio"), HackWord("demo", "a demo"), HackWord("video", "video"))


def _fqdns(candidates, kind=None):  # type: ignore[no-untyped-def]
    return [c.domain.fqdn for c in candidates if kind is None or c.kind is kind]


class TestSumanda:
    def test_exact_names_in_request_order(self) -> None:
        gen = BrandCandidates("sumanda", STARTUP, KNOWN, hack_words=WORDS)
        assert _fqdns(gen.execute(), CandidateKind.EXACT) == [
            f"sumanda.{t.suffix}" for t in STARTUP
        ]

    def test_no_suffix_hack_is_recorded(self) -> None:
        gen = BrandCandidates("sumanda", STARTUP, KNOWN, hack_words=WORDS)
        gen.execute()
        assert gen.suffix_hack_found is False

    def test_word_hacks_end_inside_the_word(self) -> None:
        gen = BrandCandidates("sumanda", STARTUP, KNOWN, hack_words=WORDS)
        hacks = [c for c in gen.execute() if c.kind is CandidateKind.HACK]
        # "video" ends in no known TLD, so it yields nothing.
        assert [(c.domain.fqdn, c.label, c.meaning) for c in hacks] == [
            ("sumandastud.io", "sumanda studio", "a studio"),
            ("sumandade.mo", "sumanda demo", "a demo"),
        ]

    def test_no_variants_unless_asked(self) -> None:
        gen = BrandCandidates("sumanda", STARTUP, KNOWN, hack_words=WORDS)
        assert _fqdns(gen.execute(), CandidateKind.VARIANT) == []

    def test_variants_are_bounded(self) -> None:
        gen = BrandCandidates("sumanda", STARTUP, KNOWN, variants=True)
        variants = [c for c in gen.execute() if c.kind is CandidateKind.VARIANT]
        tlds = {c.domain.tld.suffix for c in variants}
        assert tlds == {"com", "app", "io", "ai"}
        assert len(variants) == (1 + VARIANT_TLD_LIMIT) * (
            len(VARIANT_PREFIXES) + len(VARIANT_SUFFIXES)
        )
        labels = {c.domain.fqdn: c.label for c in variants}
        assert labels["getsumanda.com"] == "get sumanda"
        assert labels["sumandahq.ai"] == "sumanda hq"

    def test_variants_always_include_com(self) -> None:
        gen = BrandCandidates("sumanda", [TLD("io")], KNOWN, variants=True)
        tlds = {c.domain.tld.suffix for c in gen.execute() if c.kind is CandidateKind.VARIANT}
        assert tlds == {"com", "io"}

    def test_check_order_is_exact_then_hacks_then_variants(self) -> None:
        gen = BrandCandidates("sumanda", [TLD("io")], KNOWN, variants=True, hack_words=WORDS)
        kinds = [c.kind for c in gen.execute()]
        assert kinds == sorted(
            kinds, key=[CandidateKind.EXACT, CandidateKind.HACK, CandidateKind.VARIANT].index
        )

    def test_name_is_normalized(self) -> None:
        gen = BrandCandidates("  SUMANDA ", [TLD("io")], KNOWN)
        assert _fqdns(gen.execute()) == ["sumanda.io"]
        assert gen.name == "sumanda"


class TestPlato:
    def test_suffix_hack(self) -> None:
        gen = BrandCandidates("plato", [TLD("com")], KNOWN)
        candidates = gen.execute()
        assert gen.suffix_hack_found is True
        hack = next(c for c in candidates if c.kind is CandidateKind.HACK)
        assert (hack.domain.fqdn, hack.label) == ("pla.to", "plato")

    def test_each_name_appears_once(self) -> None:
        gen = BrandCandidates("plato", [TLD("to"), TLD("to")], KNOWN)
        assert _fqdns(gen.execute()) == ["plato.to", "pla.to"]


class TestValidation:
    def test_invalid_candidates_are_skipped_and_counted(self) -> None:
        # .it needs 3+ characters: "ab.it" is invalid, "ab.com" is fine.
        gen = BrandCandidates("ab", [TLD("com"), TLD("it")], KNOWN)
        assert _fqdns(gen.execute()) == ["ab.com"]
        assert gen.skipped == 1

    def test_invalid_name_yields_nothing(self) -> None:
        gen = BrandCandidates("su_manda", [TLD("com")], KNOWN, variants=True)
        assert gen.execute() == []
        assert gen.skipped > 0

    def test_known_suffix_the_tld_type_rejects_is_reported(self) -> None:
        gen = BrandCandidates("fooq", [TLD("com")], FakeKnownTlds(["q", "com"]))
        assert _fqdns(gen.execute()) == ["fooq.com"]
        assert gen.unusable_suffixes == ["q"]

    def test_word_hack_tld_must_not_reach_into_the_name(self) -> None:
        # "ab" + "c": the known suffix "bc" would split the name itself (a.bc).
        gen = BrandCandidates("ab", [], FakeKnownTlds(["bc"]), hack_words=[HackWord("c", "")])
        assert gen.execute() == []

    def test_execute_is_repeatable(self) -> None:
        gen = BrandCandidates("plato", [TLD("com")], KNOWN, hack_words=WORDS)
        assert gen.execute() == gen.execute()
        assert gen.skipped == 0


class TestResolveTldSpec:
    PRESETS: ClassVar[Presets] = {
        "startup": ["com", "app", "io"],
        "classic": ["com", "net", "org"],
        "all": "catalog",
    }

    def test_preset(self) -> None:
        sel = resolve_tld_spec("startup", self.PRESETS, lambda: [])
        assert [t.suffix for t in sel.tlds] == ["com", "app", "io"]
        assert sel.rejected == ()

    def test_presets_and_raw_suffixes_mix_without_duplicates(self) -> None:
        sel = resolve_tld_spec(" startup, .LA ,classic,,io", self.PRESETS, lambda: [])
        assert [t.suffix for t in sel.tlds] == ["com", "app", "io", "la", "net", "org"]

    def test_invalid_suffixes_are_rejected_not_fatal(self) -> None:
        sel = resolve_tld_spec("c0m,io,c0m", self.PRESETS, lambda: [])
        assert [t.suffix for t in sel.tlds] == ["io"]
        assert sel.rejected == ("c0m",)

    def test_multi_label_suffix_is_accepted_or_rejected_never_fatal(self) -> None:
        sel = resolve_tld_spec("com.ar,io", self.PRESETS, lambda: [])
        assert TLD("io") in sel.tlds
        assert ("com.ar" in sel.rejected) != any(t.suffix == "com.ar" for t in sel.tlds)

    def test_catalog_preset_is_computed_sorted_and_quiet(self) -> None:
        sel = resolve_tld_spec("all", self.PRESETS, lambda: {"to", "xn--p1ai", "io"})
        assert [t.suffix for t in sel.tlds] == ["io", "to"]
        assert sel.rejected == ()

    def test_empty(self) -> None:
        assert resolve_tld_spec(" , ", self.PRESETS, lambda: []).tlds == ()


class TestBundledData:
    def test_presets(self) -> None:
        presets = load_tld_presets()
        assert presets["startup"] == ["com", "app", "io", "ai", "co", "dev", "so", "xyz"]
        assert presets["latam"][:2] == ["com.ar", "ar"]
        assert presets["classic"] == ["com", "net", "org"]
        assert presets["all-supported"] == CATALOG_PRESET
        assert DEFAULT_PRESET in presets
        assert not any(name.startswith("_") for name in presets)

    def test_hack_words_have_meanings(self) -> None:
        words = load_hack_words()
        assert HackWord("studio", "a creative or production studio") in words
        assert all(w.word.isalpha() and w.meaning for w in words)

    @pytest.mark.parametrize("value", [42, ["com", 1], "everything"])
    def test_bad_presets_are_refused(self, value: object) -> None:
        with pytest.raises(ValueError, match="preset 'x'"):
            parse_presets({"x": value})

    def test_presets_file_is_valid_json_object(self) -> None:
        from importlib import resources

        text = resources.files("domainhack").joinpath("data/tld_presets.json").read_text()
        assert isinstance(json.loads(text), dict)
