import pytest

from domainhack.domain.entities import TLD, Availability, DomainCheckResult, DomainHack


class TestTLD:
    def test_valid_tld(self) -> None:
        tld = TLD("to")
        assert tld.suffix == "to"

    def test_rejects_single_char(self) -> None:
        with pytest.raises(ValueError):
            TLD("x")

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            TLD("")

    def test_rejects_numeric(self) -> None:
        with pytest.raises(ValueError):
            TLD("12")


class TestDomainHack:
    def test_from_word_valid(self) -> None:
        hack = DomainHack.from_word("plato", TLD("to"))
        assert hack is not None
        assert hack.word == "plato"
        assert hack.sld == "pla"
        assert hack.fqdn == "pla.to"

    def test_from_word_normalizes_case(self) -> None:
        hack = DomainHack.from_word("PLATO", TLD("to"))
        assert hack is not None
        assert hack.word == "plato"
        assert hack.sld == "pla"

    def test_from_word_strips_whitespace(self) -> None:
        hack = DomainHack.from_word("  plato  ", TLD("to"))
        assert hack is not None
        assert hack.word == "plato"

    def test_from_word_returns_none_for_short_word(self) -> None:
        assert DomainHack.from_word("to", TLD("to")) is None

    def test_from_word_returns_none_for_non_matching(self) -> None:
        assert DomainHack.from_word("hello", TLD("to")) is None

    def test_from_word_with_in_tld(self) -> None:
        hack = DomainHack.from_word("berlin", TLD("in"))
        assert hack is not None
        assert hack.sld == "berl"
        assert hack.fqdn == "berl.in"

    def test_from_sld(self) -> None:
        hack = DomainHack.from_sld("pla", TLD("to"))
        assert hack.sld == "pla"
        assert hack.word == "plato"
        assert hack.fqdn == "pla.to"

    def test_from_sld_strips_whitespace(self) -> None:
        hack = DomainHack.from_sld("  abc  ", TLD("to"))
        assert hack.sld == "abc"
        assert hack.word == "abcto"

    def test_frozen(self) -> None:
        hack = DomainHack.from_word("plato", TLD("to"))
        assert hack is not None
        with pytest.raises(AttributeError):
            hack.word = "otro"  # type: ignore[misc]


class TestDomainCheckResult:
    def test_construction(self) -> None:
        hack = DomainHack.from_word("plato", TLD("to"))
        assert hack is not None
        result = DomainCheckResult(domain=hack, availability=Availability.AVAILABLE)
        assert result.availability == Availability.AVAILABLE
        assert result.raw_title == ""
        assert result.error_message == ""
