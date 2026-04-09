from collections.abc import Iterator

from domainhack.domain.entities import TLD, DomainHack
from domainhack.ports.word_source import WordSource


class FilterWordsUseCase:
    """Filters words from a source that form valid domain hacks for a given TLD."""

    def __init__(self, word_source: WordSource, tld: TLD, min_length: int = 0) -> None:
        self._word_source = word_source
        self._tld = tld
        self._min_length = min_length

    def execute(self) -> Iterator[DomainHack]:
        for word in self._word_source.words():
            if self._min_length and len(word) < self._min_length:
                continue
            hack = DomainHack.from_word(word, self._tld)
            if hack is not None:
                yield hack
