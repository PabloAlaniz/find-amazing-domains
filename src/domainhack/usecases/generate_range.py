import itertools
import string
from collections.abc import Iterator, Sequence
from typing import Protocol

from domainhack.domain.entities import TLD, DomainHack, InvalidLabelError
from domainhack.domain.label_rules import DNS_MAX_LABEL_LENGTH, label_rule_for
from domainhack.ports.word_source import WordSource

MAX_RANGE_LENGTH = 6
_ALPHABET = string.ascii_lowercase
_BASE = len(_ALPHABET)


class GeneratedWordSource(Protocol):
    """A source that generates its words and can count them without doing so.

    ``RangeWordSource`` (every combination) and ``PatternWordSource`` (the
    names a pattern matches) are the two implementations.
    """

    def words(self) -> Iterator[str]: ...

    def count(
        self, min_length: int = 1, prefix: str = "", max_length: int | None = None
    ) -> int: ...


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
        return self.count()

    def count(self, min_length: int = 1, prefix: str = "", max_length: int | None = None) -> int:
        """How many words ``words()`` yields with ``len >= min_length`` and ``prefix``
        (and ``len <= max_length`` when given).

        Computed arithmetically: words come out shortest first and, within one
        length, in lexicographic order, so ``end_at`` cuts at a known point.
        """
        end = self._end_at
        if not end or len(end) > self._max_length or any(c not in _ALPHABET for c in end):
            end = None
        count = 0
        longest = self._max_length if max_length is None else min(self._max_length, max_length)
        for length in range(max(min_length, len(prefix), 1), longest + 1):
            free = length - len(prefix)
            if end is None or length < len(end):
                count += _BASE**free
            elif length == len(end):
                head = end[: len(prefix)]
                if head > prefix:
                    count += _BASE**free
                elif head == prefix:
                    offset = 0
                    for char in end[len(prefix) :]:
                        offset = offset * _BASE + _ALPHABET.index(char)
                    count += offset + 1
            else:
                break
        return count


class RangeCandidatesUseCase:
    """Generated candidates: every SLD of the source under every TLD, SLD-major.

    Labels the TLD's registry would refuse (see ``label_rules``, e.g. names
    shorter than 3 characters under ``.it``) are skipped and counted in
    ``skipped`` instead of being yielded, so they never reach a registrar.
    """

    def __init__(self, source: GeneratedWordSource, tlds: TLD | Sequence[TLD]) -> None:
        self._source = source
        self._tlds: tuple[TLD, ...] = (tlds,) if isinstance(tlds, TLD) else tuple(tlds)
        self.skipped = 0

    def execute(self) -> Iterator[DomainHack]:
        for sld in self._source.words():
            for tld in self._tlds:
                try:
                    yield DomainHack.from_sld(sld, tld)
                except InvalidLabelError:
                    self.skipped += 1

    def total(self) -> int:
        """Exact number of candidates ``execute()`` yields."""
        return sum(self.totals_by_tld().values())

    def totals_by_tld(self) -> dict[TLD, int]:
        """Exact number of candidates ``execute()`` yields for each TLD.

        Generated SLDs are ASCII letters, digits and single inner hyphens, so
        only the rules' lengths and ``forbidden_prefixes`` can reject them.
        """
        totals: dict[TLD, int] = {}
        for tld in self._tlds:
            rule = label_rule_for(tld.suffix)
            shortest = rule.min_length
            longest = min(rule.max_length, DNS_MAX_LABEL_LENGTH)
            count = self._source.count(shortest, "", longest)
            for prefix in rule.forbidden_prefixes:
                count -= self._source.count(shortest, prefix, longest)
            totals[tld] = count
        return totals
