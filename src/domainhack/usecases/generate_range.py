import itertools
import string
from collections.abc import Iterator, Sequence

from domainhack.domain.entities import TLD, DomainHack, InvalidLabelError
from domainhack.domain.label_rules import label_rule_for
from domainhack.ports.word_source import WordSource

MAX_RANGE_LENGTH = 6
_ALPHABET = string.ascii_lowercase
_BASE = len(_ALPHABET)


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

    def count(self, min_length: int = 1, prefix: str = "") -> int:
        """How many words ``words()`` yields with ``len >= min_length`` and ``prefix``.

        Computed arithmetically: words come out shortest first and, within one
        length, in lexicographic order, so ``end_at`` cuts at a known point.
        """
        end = self._end_at
        if not end or len(end) > self._max_length or any(c not in _ALPHABET for c in end):
            end = None
        count = 0
        for length in range(max(min_length, len(prefix), 1), self._max_length + 1):
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
    """Brute-force candidates: every generated SLD under every TLD, SLD-major.

    Labels the TLD's registry would refuse (see ``label_rules``, e.g. names
    shorter than 3 characters under ``.it``) are skipped and counted in
    ``skipped`` instead of being yielded, so they never reach a registrar.
    """

    def __init__(self, source: RangeWordSource, tlds: TLD | Sequence[TLD]) -> None:
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

        Generated SLDs are 1-6 lowercase ASCII letters, so only the rules'
        ``min_length`` and ``forbidden_prefixes`` can reject them.
        """
        totals: dict[TLD, int] = {}
        for tld in self._tlds:
            rule = label_rule_for(tld.suffix)
            count = self._source.count(min_length=rule.min_length)
            for prefix in rule.forbidden_prefixes:
                count -= self._source.count(min_length=rule.min_length, prefix=prefix)
            totals[tld] = count
        return totals
