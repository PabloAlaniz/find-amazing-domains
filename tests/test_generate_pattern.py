"""Pattern mode: ``CVCV`` and friends as a candidate source."""

import itertools

import pytest

from domainhack.domain.entities import TLD
from domainhack.usecases.generate_pattern import (
    CONSONANTS,
    MAX_PATTERN_LENGTH,
    PatternError,
    PatternWordSource,
    parse_pattern,
)
from domainhack.usecases.generate_range import RangeCandidatesUseCase

IT, TO = TLD("it"), TLD("to")


class TestParse:
    def test_classes_and_literals(self) -> None:
        assert parse_pattern("CVLDa-9") == (
            CONSONANTS,
            "aeiou",
            "abcdefghijklmnopqrstuvwxyz",
            "0123456789",
            "a",
            "-",
            "9",
        )

    def test_y_is_a_consonant(self) -> None:
        assert len(CONSONANTS) == 21
        assert "y" in CONSONANTS

    @pytest.mark.parametrize(
        "pattern",
        ["", "X", "c v", "ñ", "C.V", "-CV", "CV-", "C--V", "C" * (MAX_PATTERN_LENGTH + 1)],
    )
    def test_invalid_patterns_are_refused(self, pattern: str) -> None:
        with pytest.raises(PatternError):
            parse_pattern(pattern)

    def test_longest_pattern_is_accepted(self) -> None:
        assert len(parse_pattern("a" * MAX_PATTERN_LENGTH)) == MAX_PATTERN_LENGTH


class TestWords:
    def test_generates_in_order(self) -> None:
        words = list(PatternWordSource("CV").words())
        assert words[:6] == ["ba", "be", "bi", "bo", "bu", "ca"]
        assert words[-1] == "zu"
        assert len(words) == len(set(words)) == 21 * 5

    def test_literals_stay_put(self) -> None:
        assert list(PatternWordSource("goV").words()) == ["goa", "goe", "goi", "goo", "gou"]
        assert list(PatternWordSource("a-D").words())[:2] == ["a-0", "a-1"]

    def test_is_lazy(self) -> None:
        huge = PatternWordSource("L" * 30)
        assert list(itertools.islice(huge.words(), 2)) == ["a" * 30, "a" * 29 + "b"]

    @pytest.mark.parametrize("pattern", ["CVCV", "goCV", "LD", "abc", "C-D"])
    def test_total_matches_generation(self, pattern: str) -> None:
        source = PatternWordSource(pattern)
        assert source.total() == sum(1 for _ in source.words())

    def test_count_with_length_bounds(self) -> None:
        source = PatternWordSource("CV")
        assert source.count(min_length=3) == 0
        assert source.count(min_length=2, max_length=2) == 105
        assert source.count(max_length=1) == 0

    @pytest.mark.parametrize("prefix", ["b", "ba", "xn", "a", "bab", "b9", "babab"])
    def test_count_with_prefix_matches_generation(self, prefix: str) -> None:
        source = PatternWordSource("CVC")
        expected = sum(1 for w in source.words() if w.startswith(prefix))
        assert source.count(prefix=prefix) == expected


class TestCandidates:
    def test_every_name_under_every_tld(self) -> None:
        uc = RangeCandidatesUseCase(PatternWordSource("goV"), [TO, IT])
        assert [d.fqdn for d in uc.execute()][:4] == ["goa.to", "goa.it", "goe.to", "goe.it"]
        assert uc.skipped == 0

    @pytest.mark.parametrize("pattern", ["CV", "CVC", "xnV", "LnC", "xD"])
    def test_totals_match_generation_under_label_rules(self, pattern: str) -> None:
        # .it wants 3+ characters and refuses names starting with "xn".
        uc = RangeCandidatesUseCase(PatternWordSource(pattern), [IT, TO])
        produced = list(uc.execute())
        totals = uc.totals_by_tld()
        assert totals == {tld: sum(1 for d in produced if d.tld == tld) for tld in (IT, TO)}
        assert uc.total() == len(produced)

    def test_too_long_for_a_tld_counts_zero(self) -> None:
        # .to takes at most 61 characters.
        uc = RangeCandidatesUseCase(PatternWordSource("a" * 62 + "V"), [TO])
        assert uc.totals_by_tld() == {TO: 0}
        assert list(uc.execute()) == []
        assert uc.skipped == 5
