import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from domainhack.adapters import registrar_catalog
from domainhack.adapters.rdap_bootstrap import RdapBootstrap
from domainhack.adapters.rdap_registrar import RdapRegistrarClient
from domainhack.adapters.whois_registrar import WhoisRegistrarClient
from domainhack.domain.entities import TLD

IANA_DOC = {
    "services": [
        [["ai", "tv"], ["https://rdap.example.ai/rdap/"]],
        [["gg"], ["https://rdap.gg/"]],
    ]
}


@pytest.fixture(autouse=True)
def fake_bootstrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    boot = RdapBootstrap(
        cache_path=tmp_path / "rdap.json",
        fetcher=lambda: json.dumps(IANA_DOC).encode(),
    )
    monkeypatch.setattr(registrar_catalog, "_default_bootstrap", lambda: boot)
    yield


def test_override_tld_uses_rdap() -> None:
    client = registrar_catalog.build_registrar_for(TLD("io"), delay=0.5, timeout=7.0)
    assert isinstance(client, RdapRegistrarClient)
    assert client.base_url == "https://rdap.identitydigital.services/rdap/"
    client.close()


def test_to_uses_tonic_rdap() -> None:
    client = registrar_catalog.build_registrar_for(TLD("to"), delay=1.0)
    assert isinstance(client, RdapRegistrarClient)
    assert client.base_url == "https://rdap.tonicregistry.to/rdap/"
    client.close()


def test_bootstrap_tld_uses_rdap_over_whois() -> None:
    # .tv is in both the bootstrap and the WHOIS table: RDAP wins.
    client = registrar_catalog.build_registrar_for(TLD("tv"), delay=1.0)
    assert isinstance(client, RdapRegistrarClient)
    assert client.base_url == "https://rdap.example.ai/rdap/"
    client.close()


@pytest.mark.parametrize("tld", ["it", "gg", "la", "st"])
def test_whois_fallback(tld: str) -> None:
    client = registrar_catalog.build_registrar_for(TLD(tld), delay=1.0)
    assert isinstance(client, WhoisRegistrarClient)
    assert client.supports(tld)


def test_unsupported_tld_returns_none() -> None:
    assert registrar_catalog.build_registrar_for(TLD("es"), delay=1.0) is None


def test_supported_tlds() -> None:
    tlds = registrar_catalog.supported_tlds()
    assert {"io", "to", "ai", "tv", "it", "gg", "la", "de", "ch"} <= tlds
    assert "es" not in tlds
