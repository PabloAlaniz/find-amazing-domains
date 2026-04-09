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
