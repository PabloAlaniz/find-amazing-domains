import itertools
import string
from collections.abc import Iterator

from domainhack.ports.word_source import WordSource

MAX_RANGE_LENGTH = 6
_ALPHABET = string.ascii_lowercase
_BASE = len(_ALPHABET)


def _count_up_to(length: int) -> int:
    """Number of combinations of length 1..``length`` (sum of 26**k)."""
    count = 0
    power = 1
    for _ in range(length):
        power *= _BASE
        count += power
    return count


class RangeWordSource(WordSource):
    """Generates all lowercase letter combinations up to a given length."""

    def __init__(self, max_length: int, end_at: str | None = None) -> None:
        if max_length < 1:
            raise ValueError("max_length must be >= 1")
        if max_length > MAX_RANGE_LENGTH:
            raise ValueError(f"max_length must be <= {MAX_RANGE_LENGTH}")
        self._max_length = max_length
        self._end_at = end_at

    def words(self) -> Iterator[str]:
        for length in range(1, self._max_length + 1):
            for combo in itertools.product(_ALPHABET, repeat=length):
                word = "".join(combo)
                yield word
                if self._end_at and word == self._end_at:
                    return

    def total(self) -> int:
        """Exact number of words ``words()`` will yield, without generating them.

        If ``end_at`` can never be produced (wrong length, non-lowercase chars), the
        generator runs to exhaustion, so the full count is returned.
        """
        full = _count_up_to(self._max_length)
        end = self._end_at
        if not end or len(end) > self._max_length or any(c not in _ALPHABET for c in end):
            return full
        shorter = _count_up_to(len(end) - 1)
        offset = 0
        for char in end:
            offset = offset * _BASE + _ALPHABET.index(char)
        return shorter + offset + 1
