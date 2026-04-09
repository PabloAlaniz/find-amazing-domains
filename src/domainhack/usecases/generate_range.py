import itertools
import string
from collections.abc import Iterator

from domainhack.ports.word_source import WordSource

_MAX_ALLOWED_LENGTH = 6


class RangeWordSource(WordSource):
    """Generates all lowercase letter combinations up to a given length."""

    def __init__(self, max_length: int, end_at: str | None = None) -> None:
        if max_length < 1:
            raise ValueError("max_length must be >= 1")
        if max_length > _MAX_ALLOWED_LENGTH:
            raise ValueError(f"max_length must be <= {_MAX_ALLOWED_LENGTH}")
        self._max_length = max_length
        self._end_at = end_at

    def words(self) -> Iterator[str]:
        for length in range(1, self._max_length + 1):
            for combo in itertools.product(string.ascii_lowercase, repeat=length):
                word = "".join(combo)
                yield word
                if self._end_at and word == self._end_at:
                    return
