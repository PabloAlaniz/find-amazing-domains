from domainhack.domain.entities import TLD
from domainhack.usecases.filter_words import FilterWordsUseCase
from tests.conftest import FakeWordSource


class TestFilterWordsUseCase:
    def test_filters_matching_words(self) -> None:
        source = FakeWordSource(["plato", "grato", "hello", "abeto"])
        uc = FilterWordsUseCase(source, TLD("to"))
        hacks = list(uc.execute())
        assert len(hacks) == 3
        assert {h.word for h in hacks} == {"plato", "grato", "abeto"}

    def test_min_length(self) -> None:
        source = FakeWordSource(["plato", "grato", "abasto"])
        uc = FilterWordsUseCase(source, TLD("to"), min_length=6)
        hacks = list(uc.execute())
        assert len(hacks) == 1
        assert hacks[0].word == "abasto"

    def test_no_matches(self) -> None:
        source = FakeWordSource(["hello", "world", "python"])
        uc = FilterWordsUseCase(source, TLD("to"))
        hacks = list(uc.execute())
        assert hacks == []

    def test_empty_source(self) -> None:
        source = FakeWordSource([])
        uc = FilterWordsUseCase(source, TLD("to"))
        hacks = list(uc.execute())
        assert hacks == []

    def test_with_in_tld(self) -> None:
        source = FakeWordSource(["berlin", "cabin", "hello"])
        uc = FilterWordsUseCase(source, TLD("in"))
        hacks = list(uc.execute())
        assert len(hacks) == 2
        assert {h.fqdn for h in hacks} == {"berl.in", "cab.in"}
