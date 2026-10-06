"""Decide which candidates are checked first, and how many per TLD.

Registries throttle (whois.nic.it allowed about 50 queries in a live run), so
the queries a run gets should go to the best candidates, not to whatever comes
first alphabetically.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from enum import Enum

from domainhack.domain.entities import TLD, DomainHack

ScoreKey = tuple[float, ...]
Scorer = Callable[[DomainHack], ScoreKey]
"""Maps a candidate to a sort key; lower sorts first (checked earlier)."""


class CandidateOrder(str, Enum):
    SCORE = "score"  # best candidates first, by ``Scorer``
    ALPHA = "alpha"  # alphabetical by domain name
    INPUT = "input"  # as produced (word-list order, or range generation order)


def length_score(domain: DomainHack) -> ScoreKey:
    """The default score: shorter SLD first, then shorter full word.

    This is the hook for smarter ranking: a word-frequency or
    pronounceability scorer only has to return a different key (for example
    ``(-frequency, len(sld))``) and be passed as ``scorer``.
    """
    return (len(domain.sld), len(domain.word))


def _tiebreak(domain: DomainHack) -> tuple[str, str, str]:
    # References to strings the DomainHack already holds: sorting a large
    # list does not build a new name string per candidate.
    return (domain.ascii_sld, domain.tld.suffix, domain.word)


class RankCandidatesUseCase:
    """Orders candidates and caps how many are checked per TLD.

    * ``SCORE``: by ``scorer`` (default ``length_score``), ties broken
      alphabetically by name, then TLD, then word, so the order is total and
      deterministic whatever the input order.
    * ``ALPHA``: alphabetically by name, then TLD, then word.
    * ``INPUT``: unchanged and lazy, so it can wrap the huge range generator.

    ``SCORE`` and ``ALPHA`` materialize the candidates (DomainHack objects
    only), which suits bounded word lists, not range mode.

    ``limit`` keeps at most that many candidates per TLD, counted after
    ordering. When ``tlds`` is given, a lazy input stops being read as soon as
    every one of them has reached the limit.
    """

    def __init__(
        self,
        order: CandidateOrder = CandidateOrder.SCORE,
        *,
        limit: int | None = None,
        scorer: Scorer = length_score,
        tlds: Sequence[TLD] | None = None,
    ) -> None:
        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")
        self._order = order
        self._limit = limit
        self._scorer = scorer
        self._tlds = frozenset(tlds) if tlds is not None else None

    @property
    def materializes(self) -> bool:
        """True when ``execute`` has to read every candidate before yielding one."""
        return self._order is not CandidateOrder.INPUT

    def execute(self, candidates: Iterable[DomainHack]) -> Iterator[DomainHack]:
        return self._limited(self._ordered(candidates))

    def _ordered(self, candidates: Iterable[DomainHack]) -> Iterable[DomainHack]:
        if self._order is CandidateOrder.INPUT:
            return candidates
        if self._order is CandidateOrder.ALPHA:
            return sorted(candidates, key=_tiebreak)
        scorer = self._scorer
        return sorted(candidates, key=lambda d: (scorer(d), _tiebreak(d)))

    def _limited(self, candidates: Iterable[DomainHack]) -> Iterator[DomainHack]:
        limit = self._limit
        if limit is None:
            yield from candidates
            return
        counts: dict[TLD, int] = {}
        pending = set(self._tlds) if self._tlds is not None else None
        for domain in candidates:
            seen = counts.get(domain.tld, 0)
            if seen >= limit:
                continue
            counts[domain.tld] = seen + 1
            yield domain
            if pending is not None and seen + 1 == limit:
                pending.discard(domain.tld)
                if not pending:
                    return
