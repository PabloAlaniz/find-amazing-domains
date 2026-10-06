"""IanaTldList: the bundled IANA TLD list plus curated second-level suffixes."""

import json
from importlib import resources

import pytest

from domainhack.adapters.iana_tlds import (
    IanaTldList,
    load_iana_snapshot,
    load_second_level,
    parse_iana_tlds,
)
from domainhack.domain.entities import TLD
from domainhack.ports.known_tlds import KnownTlds


@pytest.fixture(scope="module")
def known() -> IanaTldList:
    return IanaTldList()


class TestBundledData:
    def test_snapshot_keeps_the_version_header(self) -> None:
        text = resources.files("domainhack").joinpath("data/iana_tlds.txt").read_text()
        assert text.startswith("# Version ")
        version, tlds = load_iana_snapshot()
        assert version.startswith("Version ")
        assert len(tlds) > 1000
        assert all(t == t.lower() for t in tlds)
        assert {"com", "to", "io", "ar", "studio", "xn--p1ai"} <= set(tlds)

    def test_second_level_is_latam_first_and_cites_its_source(self) -> None:
        raw = json.loads(
            resources.files("domainhack").joinpath("data/second_level.json").read_text()
        )
        assert "publicsuffix.org" in raw["source"]
        suffixes = load_second_level()
        assert suffixes[:3] == ["com.ar", "net.ar", "org.ar"]
        assert {"com.mx", "com.br", "com.co", "com.pe", "co.uk"} <= set(suffixes)
        assert len(suffixes) == len(set(suffixes))
        for suffix in suffixes:
            TLD(suffix)  # every entry is a valid multi-label TLD

    def test_parse_skips_comments_and_blank_lines(self) -> None:
        version, tlds = parse_iana_tlds("# Version 1\n# other\n\nCOM\n  XN--P1AI \n")
        assert version == "Version 1"
        assert tlds == ["com", "xn--p1ai"]


class TestIsKnown:
    def test_is_a_known_tlds_port(self, known: IanaTldList) -> None:
        assert isinstance(known, KnownTlds)
        assert known.version.startswith("Version ")

    @pytest.mark.parametrize("suffix", ["to", "IO", ".com", "com.ar", "CO.UK", "xn--p1ai"])
    def test_known(self, known: IanaTldList, suffix: str) -> None:
        assert known.is_known(suffix)

    @pytest.mark.parametrize("suffix", ["sumanda", "com.zz", "zz", "", "ar.com"])
    def test_unknown(self, known: IanaTldList, suffix: str) -> None:
        assert not known.is_known(suffix)

    def test_properties(self, known: IanaTldList) -> None:
        assert "to" in known.top_level_domains
        assert "com.ar" not in known.top_level_domains
        assert known.second_level_suffixes[0] == "com.ar"


class TestSuffixesOf:
    def test_plato(self, known: IanaTldList) -> None:
        assert known.suffixes_of("plato") == ["to"]

    def test_sumanda_has_no_hack(self, known: IanaTldList) -> None:
        assert known.suffixes_of("sumanda") == []

    def test_fotocomar_includes_com_ar(self, known: IanaTldList) -> None:
        assert known.suffixes_of("fotocomar") == ["com.ar", "ar"]

    def test_longest_first(self, known: IanaTldList) -> None:
        assert known.suffixes_of("sumandastudio") == ["studio", "io"]
        assert known.suffixes_of("testing") == ["ing", "ng"]

    def test_never_the_whole_name(self, known: IanaTldList) -> None:
        assert known.suffixes_of("to") == []
        assert known.suffixes_of("studio") == ["io"]

    def test_case_and_whitespace(self, known: IanaTldList) -> None:
        assert known.suffixes_of("  PLATO ") == ["to"]

    def test_injected_lists(self) -> None:
        small = IanaTldList(tlds=["TO", "ar", "uk"], second_level=["com.ar", "co.uk", ".CO.UK"])
        assert small.version == ""
        assert small.second_level_suffixes == ("com.ar", "co.uk")
        assert small.suffixes_of("fotocomar") == ["com.ar", "ar"]
        assert small.suffixes_of("barcouk") == ["co.uk", "uk"]
        assert small.suffixes_of("plato") == ["to"]
        assert not small.is_known("io")
