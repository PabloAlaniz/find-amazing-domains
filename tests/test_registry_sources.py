"""The bundled registry data and its loader (data/registry_sources.json)."""

import re
from typing import Any

import pytest

from domainhack.adapters import rdap_bootstrap, whois_registrar
from domainhack.adapters.registry_sources import (
    RegistrySourcesError,
    describe_unsupported,
    load_registry_sources,
    parse_registry_sources,
    unsupported_message,
)


def _doc(**tlds: Any) -> dict[str, Any]:
    return {"rdap_denylist": {"tlds": ["GG"], "reason": "broken"}, "tlds": tlds}


VERIFIED = {"date": "2026-10-07", "taken_probe": "nic.xx", "free_probe": "zz.xx"}
WHOIS = {"host": "whois.nic.xx", "not_found": "No match", "taken": None}


class TestBundledData:
    def test_loads_and_every_pattern_compiles(self) -> None:
        sources = load_registry_sources()
        assert sources.tlds, "registry_sources.json must not be empty"
        for spec in [*sources.whois_primary().values(), *sources.whois_fallback().values()]:
            re.compile(spec.not_found)
            if spec.taken:
                re.compile(spec.taken)

    def test_every_unsupported_entry_explains_why(self) -> None:
        for source in load_registry_sources().tlds.values():
            if source.status in ("restricted", "unavailable"):
                assert source.reason, f".{source.tld} needs a reason"

    def test_module_tables_come_from_the_data(self) -> None:
        sources = load_registry_sources()
        assert sources.rdap_overrides() == rdap_bootstrap.RDAP_OVERRIDES
        assert sources.rdap_denylist == rdap_bootstrap.RDAP_DENYLIST
        assert set(whois_registrar.WHOIS_SERVERS) == set(sources.whois_primary())
        assert set(whois_registrar.WHOIS_FALLBACK_SERVERS) == set(sources.whois_fallback())

    def test_known_entries(self) -> None:
        sources = load_registry_sources()
        assert sources.rdap_overrides()["to"] == "https://rdap.tonicregistry.to/rdap/"
        assert sources.rdap_denylist >= {"gg", "la"}
        it = sources.whois_primary()["it"]
        assert (it.host, it.min_interval) == ("whois.nic.it", 4.0)
        assert "ar" in sources.whois_fallback()


class TestParse:
    def test_statuses_route_to_tables(self) -> None:
        sources = parse_registry_sources(
            _doc(
                aa={"status": "rdap", "rdap": "https://rdap.nic.aa/", "verified": VERIFIED},
                bb={"status": "whois", "whois": WHOIS, "verified": VERIFIED},
                cc={"status": "rdap", "whois": WHOIS, "verified": VERIFIED},
                dd={"status": "restricted", "reason": "approved IPs only"},
                ee={"status": "unavailable", "reason": "no public WHOIS"},
            )
        )
        assert sources.rdap_overrides() == {"aa": "https://rdap.nic.aa/"}
        assert set(sources.whois_primary()) == {"bb"}
        assert set(sources.whois_fallback()) == {"cc"}
        assert sources.rdap_denylist == frozenset({"gg"})
        assert sources.unsupported_reason("DD") == "restricted: approved IPs only"
        assert sources.unsupported_reason("ee") == "unavailable: no public WHOIS"
        assert sources.unsupported_reason("bb") == ""
        assert sources.unsupported_reason("zz") == ""
        whois = sources.whois_primary()["bb"]
        assert (whois.query_format, whois.min_interval) == ("{fqdn}\r\n", 1.0)

    @pytest.mark.parametrize(
        ("entry", "message"),
        [
            ({"status": "maybe"}, "unknown status"),
            ({"status": "whois", "verified": VERIFIED}, "needs a whois server"),
            ({"status": "restricted"}, "needs a reason"),
            ({"status": "whois", "whois": WHOIS}, "verified"),
            ({"status": "rdap", "rdap": "http://x/", "verified": VERIFIED}, "https://"),
            ({"status": "rdap", "rdap": "https://x", "verified": VERIFIED}, "end with '/'"),
            (
                {"status": "whois", "whois": {**WHOIS, "not_found": "("}, "verified": VERIFIED},
                "invalid not_found regex",
            ),
            (
                {"status": "whois", "whois": {"host": "h"}, "verified": VERIFIED},
                "host and not_found",
            ),
            (
                {
                    "status": "whois",
                    "whois": {**WHOIS, "query_format": "{fqdn}"},
                    "verified": VERIFIED,
                },
                "CRLF",
            ),
            ("nope", "must be an object"),
        ],
    )
    def test_rejects_malformed_entries(self, entry: Any, message: str) -> None:
        with pytest.raises(RegistrySourcesError, match=re.escape(message)):
            parse_registry_sources(_doc(xx=entry))

    def test_rejects_document_without_tlds(self) -> None:
        with pytest.raises(RegistrySourcesError, match="tlds"):
            parse_registry_sources({"tld": {}})


class TestMessages:
    def test_unknown_tld_gets_plain_message(self) -> None:
        assert unsupported_message("zz") == "no registrar supports .zz"

    def test_grouping_keeps_plain_ones_together(self) -> None:
        assert describe_unsupported(["zz", "yy"]) == ["no registrar supports .zz, .yy"]
        assert describe_unsupported([]) == []
