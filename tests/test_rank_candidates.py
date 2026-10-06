import itertools
from collections.abc import Iterator

import pytest

from domainhack.domain.entities import TLD, DomainHack
from domainhack.usecases.rank_candidates import (
    CandidateOrder,
    RankCandidatesUseCase,
    ScoreKey,
    length_score,
)
from tests.fakes import hack

TO, IO = TLD("to"), TLD("io")


def _word(word: str, tld: TLD = TO) -> DomainHack:
    domain = DomainHack.from_word(word, tld)
    assert domain is not None
    return domain


def _fqdns(domains: Iterator[DomainHack]) -> list[str]:
    return [d.fqdn for d in domains]


class TestScoreOrder:
    def test_shorter_sld_first_then_shorter_word_then_alphabetical(self) -> None:
        candidates = [
            _word("monito"),  # moni.to: SLD 4
            _word("plato"),  # pla.to: SLD 3, word 5
            _word("gato"),  # ga.to: SLD 2
            _word("esto"),  # es.to: SLD 2
            _word("radio", IO),  # rad.io: SLD 3, word 5
            _word("manto"),  # man.to: SLD 3, word 5
        ]
        ranked = _fqdns(RankCandidatesUseCase().execute(candidates))
        assert ranked == ["es.to", "ga.to", "man.to", "pla.to", "rad.io", "moni.to"]

    def test_word_length_breaks_sld_ties(self) -> None:
        # Same SLD under two TLDs: the shorter word wins although ".aero" < ".to".
        short = _word("plato")
        long_ = _word("plaaero", TLD("aero"))
        ranked = list(RankCandidatesUseCase().execute([long_, short]))
        assert ranked == [short, long_]

    def test_is_deterministic_whatever_the_input_order(self) -> None:
        candidates = [_word(w) for w in ("gato", "esto", "plato", "manto", "hito")]
        candidates += [_word("radio", IO), hack("pla", "io")]
        expected = list(RankCandidatesUseCase().execute(candidates))
        for permutation in itertools.permutations(candidates):
            assert list(RankCandidatesUseCase().execute(permutation)) == expected

    def test_full_ties_are_broken_by_tld_then_word(self) -> None:
        # Same SLD and word length under different TLDs: alphabetical by TLD.
        a, b = hack("pla", "to"), hack("pla", "io")
        assert _fqdns(RankCandidatesUseCase().execute([a, b])) == ["pla.io", "pla.to"]
        # Same fqdn from two words (a duplicate in the list): word decides.
        x = DomainHack(word="plato", sld="pla", tld=TO)
        y = DomainHack(word="plat0", sld="pla", tld=TO)
        assert list(RankCandidatesUseCase().execute([x, y])) == [y, x]

    def test_custom_scorer_hook(self) -> None:
        frequency = {"pla": 10.0, "es": 1.0}

        def by_frequency(domain: DomainHack) -> ScoreKey:
            return (-frequency.get(domain.sld, 0.0), *length_score(domain))

        ranker = RankCandidatesUseCase(scorer=by_frequency)
        ranked = _fqdns(ranker.execute([_word("esto"), _word("plato"), _word("gato")]))
        assert ranked == ["pla.to", "es.to", "ga.to"]

    def test_default_score_is_length_based(self) -> None:
        assert length_score(_word("plato")) == (3, 5)


class TestOtherOrders:
    def test_alpha_sorts_by_name(self) -> None:
        candidates = [_word("plato"), _word("esto"), _word("radio", IO), _word("monito")]
        ranker = RankCandidatesUseCase(CandidateOrder.ALPHA)
        assert _fqdns(ranker.execute(candidates)) == ["es.to", "moni.to", "pla.to", "rad.io"]
        assert ranker.materializes

    def test_input_keeps_order_and_is_lazy(self) -> None:
        def endless() -> Iterator[DomainHack]:
            for n in itertools.count():
                yield hack(f"a{n}", "to")

        ranker = RankCandidatesUseCase(CandidateOrder.INPUT)
        assert not ranker.materializes
        assert _fqdns(itertools.islice(ranker.execute(endless()), 3)) == ["a0.to", "a1.to", "a2.to"]


class TestLimit:
    def test_limit_is_per_tld_after_ordering(self) -> None:
        candidates = [
            _word("monito"),
            _word("plato"),
            _word("gato"),
            _word("esto"),
            _word("radio", IO),
            _word("audio", IO),
            _word("patio", IO),
        ]
        ranked = _fqdns(RankCandidatesUseCase(limit=2).execute(candidates))
        assert ranked == ["es.to", "ga.to", "aud.io", "pat.io"]

    def test_lazy_input_stops_once_every_tld_is_full(self) -> None:
        pulled: list[str] = []

        def endless() -> Iterator[DomainHack]:
            for n in itertools.count():
                for tld in ("to", "io"):
                    pulled.append(f"a{n}.{tld}")
                    yield hack(f"a{n}", tld)

        ranker = RankCandidatesUseCase(CandidateOrder.INPUT, limit=2, tlds=[TO, IO])
        assert _fqdns(ranker.execute(endless())) == ["a0.to", "a0.io", "a1.to", "a1.io"]
        assert len(pulled) == 4  # the generator was not read any further

    def test_without_tlds_the_whole_input_is_read(self) -> None:
        candidates = [hack("a", "to"), hack("b", "to"), hack("c", "io")]
        ranker = RankCandidatesUseCase(CandidateOrder.INPUT, limit=1)
        assert _fqdns(ranker.execute(candidates)) == ["a.to", "c.io"]

    def test_a_tld_with_fewer_candidates_than_the_limit(self) -> None:
        candidates = [hack("a", "to"), hack("b", "to"), hack("c", "io")]
        ranker = RankCandidatesUseCase(CandidateOrder.INPUT, limit=2, tlds=[TO, IO])
        assert _fqdns(ranker.execute(candidates)) == ["a.to", "b.to", "c.io"]

    @pytest.mark.parametrize("limit", [0, -1])
    def test_limit_must_be_positive(self, limit: int) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            RankCandidatesUseCase(limit=limit)
