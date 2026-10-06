import pytest

from domainhack.usecases.generate_range import RangeWordSource


class TestRangeWordSource:
    def test_single_char(self) -> None:
        source = RangeWordSource(max_length=1)
        words = list(source.words())
        assert len(words) == 26
        assert words[0] == "a"
        assert words[25] == "z"

    def test_two_chars(self) -> None:
        source = RangeWordSource(max_length=2)
        words = list(source.words())
        # 26 single + 26*26 double = 702
        assert len(words) == 702
        assert words[0] == "a"
        assert words[26] == "aa"
        assert words[-1] == "zz"

    def test_end_at(self) -> None:
        source = RangeWordSource(max_length=2, end_at="ac")
        words = list(source.words())
        # a-z (26) + aa, ab, ac (3) = 29
        assert len(words) == 29
        assert words[-1] == "ac"

    def test_end_at_single_char(self) -> None:
        source = RangeWordSource(max_length=1, end_at="c")
        words = list(source.words())
        assert words == ["a", "b", "c"]

    def test_invalid_max_length(self) -> None:
        with pytest.raises(ValueError):
            RangeWordSource(max_length=0)

    def test_max_length_exceeds_limit(self) -> None:
        with pytest.raises(ValueError, match="<= 6"):
            RangeWordSource(max_length=7)

    def test_max_length_at_limit(self) -> None:
        source = RangeWordSource(max_length=6)
        assert source._max_length == 6

    def test_is_lazy_generator(self) -> None:
        source = RangeWordSource(max_length=3)
        gen = source.words()
        assert next(gen) == "a"
        assert next(gen) == "b"


class TestRangeWordSourceTotal:
    @pytest.mark.parametrize(
        ("max_length", "end_at"),
        [
            (1, None),
            (2, None),
            (3, None),
            (1, "c"),
            (2, "ac"),
            (2, "z"),
            (2, "zz"),
            (3, "ba"),
            (3, "mzq"),
            (2, "abc"),  # longer than max_length: never matched, runs to exhaustion
            (2, "A"),  # not lowercase: never matched
            (2, "a1"),
            (2, ""),
        ],
    )
    def test_matches_generated_count(self, max_length: int, end_at: str | None) -> None:
        source = RangeWordSource(max_length=max_length, end_at=end_at)
        assert source.total() == len(list(source.words()))

    def test_max_length_six(self) -> None:
        assert RangeWordSource(max_length=6).total() == sum(26**k for k in range(1, 7))

    def test_end_at_within_six(self) -> None:
        # 26 + 26**2 + 26**3 (all shorter) + index of "aaaa" (0) + 1
        assert RangeWordSource(max_length=6, end_at="aaaa").total() == 26 + 676 + 17576 + 1
