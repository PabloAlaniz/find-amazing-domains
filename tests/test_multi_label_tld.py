"""Multi-label public suffixes (com.ar, co.uk...): parsing, labels, routing."""

import argparse
import json
from pathlib import Path

import httpx
import pytest

from domainhack.adapters import registrar_catalog
from domainhack.adapters.cached_registrar import CachedRegistrarClient
from domainhack.adapters.rdap_bootstrap import RdapBootstrap
from domainhack.adapters.rdap_registrar import RdapRegistrarClient
from domainhack.adapters.whois_registrar import WHOIS_SERVERS, WhoisRegistrarClient
from domainhack.cli.app import build_parser, main, parse_tld_list
from domainhack.domain.entities import TLD, Availability, DomainHack, InvalidLabelError
from domainhack.domain.label_rules import DEFAULT_LABEL_RULE, label_rule_for
from tests.fakes import FakeCatalog, ScriptedRegistrar

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rdap"
COM_AR = TLD("com.ar")


class TestTldParsing:
    @pytest.mark.parametrize(
        "suffix", ["com.ar", "net.ar", "com.mx", "com.br", "co.uk", "com.co", "com.pe", "ne.jp"]
    )
    def test_accepts_second_level_suffixes(self, suffix: str) -> None:
        assert TLD(suffix).suffix == suffix

    @pytest.mark.parametrize(
        "suffix",
        [
            "co..uk",  # empty label
            ".ar",
            "com.",
            "comm.ar",  # second-level label longer than 3
            "c.ar",  # second-level label shorter than 2
            "com.a",  # top-level label shorter than 2
            "co1.uk",
            "co-.uk",
            "a.co.uk",  # three labels
            "com.ar.x",
            "çom.ar",
        ],
    )
    def test_rejects_invalid_labels(self, suffix: str) -> None:
        with pytest.raises(ValueError):
            TLD(suffix)

    def test_labels_and_top_level(self) -> None:
        assert COM_AR.labels == ("com", "ar")
        assert COM_AR.top_level == "ar"
        assert COM_AR.is_multi_label
        assert COM_AR.joined == "comar"
        assert TLD("to").labels == ("to",)
        assert TLD("to").top_level == "to"
        assert not TLD("to").is_multi_label


class TestDomainHackMultiLabel:
    def test_from_sld(self) -> None:
        domain = DomainHack.from_sld("sumanda", COM_AR)
        assert domain.fqdn == "sumanda.com.ar"
        assert domain.display == "sumanda.com.ar"
        assert domain.word == "sumandacomar"

    def test_from_word_concatenates_without_dots(self) -> None:
        domain = DomainHack.from_word("fotocomar", COM_AR)
        assert domain is not None
        assert domain.sld == "foto"
        assert domain.fqdn == "foto.com.ar"

    def test_from_word_requires_the_joined_suffix(self) -> None:
        assert DomainHack.from_word("foto.com.ar", COM_AR) is None
        assert DomainHack.from_word("comar", COM_AR) is None
        assert DomainHack.from_word("fotoar", COM_AR) is None

    def test_from_word_round_trips_from_sld(self) -> None:
        domain = DomainHack.from_sld("sumanda", COM_AR)
        assert DomainHack.from_word(domain.word, COM_AR) == domain

    def test_nic_ar_rules_allow_idn_and_cap_length(self) -> None:
        domain = DomainHack.from_sld("ñandú", COM_AR)
        assert domain.fqdn == "xn--and-6ma2c.com.ar"
        DomainHack.from_sld("a" * 50, COM_AR)
        with pytest.raises(InvalidLabelError, match=r"\.com\.ar"):
            DomainHack.from_sld("a" * 51, COM_AR)

    def test_other_second_level_suffixes_use_the_default_rule(self) -> None:
        assert label_rule_for("co.uk") is DEFAULT_LABEL_RULE
        assert label_rule_for(".COM.AR").max_length == 50
        with pytest.raises(InvalidLabelError):
            DomainHack.from_sld("ñandú", TLD("co.uk"))

    def test_cache_key_is_the_full_fqdn(self, tmp_path: Path) -> None:
        inner = ScriptedRegistrar({"sumanda.com.ar": Availability.AVAILABLE})
        with CachedRegistrarClient(inner, path=tmp_path / "cache.sqlite3") as cache:
            first = cache.check_availability(DomainHack.from_sld("sumanda", COM_AR))
            again = cache.check_availability(DomainHack.from_sld("sumanda", COM_AR))
            other = cache.check_availability(DomainHack.from_sld("sumanda", TLD("ar")))
        assert first.availability is again.availability is Availability.AVAILABLE
        assert other.availability is Availability.TAKEN
        assert inner.calls == ["sumanda.com.ar", "sumanda.ar"]


class TestCli:
    def test_parse_tld_list_accepts_multi_label(self) -> None:
        assert parse_tld_list(".COM.AR,to,com.ar") == [COM_AR, TLD("to")]

    def test_parse_tld_list_names_the_bad_suffix(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="comm.ar"):
            parse_tld_list("to,comm.ar")

    def test_parser_accepts_tld_com_ar(self) -> None:
        args = build_parser().parse_args(["--tld", "com.ar", "check", "--range-max", "1"])
        assert args.tld == [COM_AR]

    def test_check_routes_com_ar(self, capsys: pytest.CaptureFixture[str]) -> None:
        registrar = ScriptedRegistrar(default=Availability.AVAILABLE)
        argv = ["--tld", "com.ar", "check", "--range-max", "1", "--range-end", "b"]
        argv += ["--no-progress", "--no-cache"]
        assert main(argv, catalog=FakeCatalog(registrar)) == 0
        assert registrar.calls == ["a.com.ar", "b.com.ar"]
        assert "AVAILABLE: a.com.ar" in capsys.readouterr().out


IANA_DOC = {"services": [[["ar"], ["https://rdap.nic.ar/"]], [["ai"], ["https://rdap.ai/"]]]}


@pytest.fixture
def boot(tmp_path: Path) -> RdapBootstrap:
    return RdapBootstrap(
        cache_path=tmp_path / "rdap.json",
        fetcher=lambda: json.dumps(IANA_DOC).encode(),
        snapshot=lambda: {},
    )


class TestRouting:
    def test_com_ar_uses_the_ar_rdap_server(self, boot: RdapBootstrap) -> None:
        assert boot.base_url_for("com.ar") == "https://rdap.nic.ar/"
        client = registrar_catalog.build_registrar_for(COM_AR, delay=0, bootstrap=boot)
        assert isinstance(client, RdapRegistrarClient)
        assert client.base_url == "https://rdap.nic.ar/"
        client.close()

    def test_own_entry_wins_over_the_top_level(self, tmp_path: Path) -> None:
        doc = {"services": [[["ar"], ["https://a/"]], [["com.ar"], ["https://b/"]]]}
        boot = RdapBootstrap(
            cache_path=tmp_path / "x.json", fetcher=lambda: json.dumps(doc).encode()
        )
        assert boot.base_url_for("com.ar") == "https://b/"

    def test_override_and_denylist_follow_the_top_level(self, boot: RdapBootstrap) -> None:
        assert boot.base_url_for("com.co") == "https://rdap.registry.co/co/"
        assert boot.base_url_for("com.la") is None
        assert boot.base_url_for("com.zz") is None

    def test_whois_top_level_serves_second_level(self, boot: RdapBootstrap) -> None:
        client = registrar_catalog.build_registrar_for(TLD("com.mx"), delay=0, bootstrap=boot)
        assert isinstance(client, WhoisRegistrarClient)
        assert client.server_for("com.mx") is WHOIS_SERVERS["mx"]
        assert client.supports("com.mx")
        assert not client.supports("com.zz")

    def test_unsupported_second_level(self, boot: RdapBootstrap) -> None:
        assert registrar_catalog.build_registrar_for(TLD("co.zz"), delay=0, bootstrap=boot) is None

    def test_rdap_query_names_the_whole_domain(self) -> None:
        """Fixture: rdap.nic.ar's live answer for google.com.ar (2026-10-06), trimmed."""
        body = (FIXTURES / "nicar_google_com_ar.json").read_bytes()
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if request.url.path == "/domain/google.com.ar":
                return httpx.Response(200, content=body)
            return httpx.Response(404)

        client = RdapRegistrarClient(
            "https://rdap.nic.ar/",
            delay=0,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        taken = client.check_availability(DomainHack.from_sld("google", COM_AR))
        free = client.check_availability(DomainHack.from_sld("sumanda", COM_AR))
        assert seen == [
            "https://rdap.nic.ar/domain/google.com.ar",
            "https://rdap.nic.ar/domain/sumanda.com.ar",
        ]
        assert taken.availability is Availability.TAKEN
        assert taken.statuses == ("active",)
        assert taken.expires_at is not None
        assert taken.expires_at.date().isoformat() == "2027-07-08"
        assert free.availability is Availability.AVAILABLE
