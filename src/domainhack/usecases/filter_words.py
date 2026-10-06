from collections.abc import Iterator, Sequence

from domainhack.domain.entities import TLD, DomainHack, InvalidLabelError
from domainhack.ports.word_source import WordSource


class FilterWordsUseCase:
    """Filters words from a source that form valid domain hacks for one or more TLDs.

    A word may match several TLDs (e.g. "testing" -> "testi.ng" and "test.ing");
    one DomainHack is yielded per match, in word order and then in TLD order.

    Matches whose SLD is not a valid label for that TLD (``can't`` -> ``can't.to``,
    an IDN under a TLD without IDN support, too short for ``.it``...) are not
    yielded; they are counted in ``skipped`` so callers can report them.
    """

    def __init__(
        self, word_source: WordSource, tld: TLD | Sequence[TLD], min_length: int = 0
    ) -> None:
        self._word_source = word_source
        self._tlds: tuple[TLD, ...] = (tld,) if isinstance(tld, TLD) else tuple(tld)
        self._min_length = min_length
        self.skipped = 0

    def execute(self) -> Iterator[DomainHack]:
        for word in self._word_source.words():
            if self._min_length and len(word) < self._min_length:
                continue
            for tld in self._tlds:
                try:
                    hack = DomainHack.from_word(word, tld)
                except InvalidLabelError:
                    self.skipped += 1
                    continue
                if hack is not None:
                    yield hack
