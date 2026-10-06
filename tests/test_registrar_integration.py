"""Live checks against real RDAP / WHOIS servers.

Run manually with: pytest -m integration -k "rdap or whois" -v
"""

import pytest

from domainhack.adapters.rdap_bootstrap import RdapBootstrap
from domainhack.adapters.rdap_registrar import RdapRegistrarClient
from domainhack.adapters.registrar_catalog import build_registrar_for
from domainhack.adapters.whois_registrar import WhoisRegistrarClient
from domainhack.domain.entities import TLD, Availability, DomainHack

pytestmark = pytest.mark.integration

RANDOM_SLD = "zqxv7kq3mplw"


def _check(tld: str, sld: str) -> Availability:
    client = build_registrar_for(TLD(tld), delay=1.0, timeout=20.0)
    assert client is not None
    with client:
        return client.check_availability(DomainHack.from_sld(sld, TLD(tld))).availability


@pytest.mark.parametrize("tld", ["io", "to", "co", "ai"])
def test_rdap_taken_and_available(tld: str) -> None:
    assert _check(tld, "google") == Availability.TAKEN
    assert _check(tld, RANDOM_SLD) == Availability.AVAILABLE


def test_rdap_direct_client() -> None:
    with RdapRegistrarClient(
        "https://rdap.identitydigital.services/rdap/", delay=1.0, timeout=20.0
    ) as client:
        result = client.check_availability(DomainHack.from_sld("google", TLD("io")))
    assert result.availability == Availability.TAKEN
    assert result.raw_title == "HTTP 200"


def test_rdap_live_bootstrap() -> None:
    boot = RdapBootstrap()
    assert boot.base_url_for("ai") == "https://rdap.identitydigital.services/rdap/"
    assert boot.base_url_for("fm") is not None


@pytest.mark.parametrize("tld", ["it", "st", "gg", "la", "nu"])
def test_whois_taken_and_available(tld: str) -> None:
    with WhoisRegistrarClient(delay=1.0, timeout=20.0) as client:
        taken = client.check_availability(DomainHack.from_sld("google", TLD(tld)))
        free = client.check_availability(DomainHack.from_sld(RANDOM_SLD, TLD(tld)))
    assert taken.availability == Availability.TAKEN, taken
    assert free.availability == Availability.AVAILABLE, free
