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


def _weekly_sample(size: int = 8) -> list[str]:
    """A rotating slice of the registries added from registry_sources.json, so the
    weekly live job re-verifies every one of them over time without hammering any."""
    import datetime

    from domainhack.adapters.registry_sources import load_registry_sources

    tlds = sorted(
        t
        for t, s in load_registry_sources().tlds.items()
        if s.status in ("rdap", "whois") and s.verified_taken and s.verified_free and t != "it"
    )
    week = datetime.date.today().isocalendar()[1]
    start = (week * size) % len(tlds)
    return [tlds[(start + i) % len(tlds)] for i in range(size)]


@pytest.mark.integration
@pytest.mark.parametrize("tld", _weekly_sample())
def test_registry_source_still_answers_like_when_verified(tld: str) -> None:
    from domainhack.adapters.registry_sources import load_registry_sources

    source = load_registry_sources().tlds[tld]
    client = build_registrar_for(TLD(tld), delay=2.0, timeout=20.0)
    assert client is not None
    with client:
        for probe, expected in (
            (source.verified_taken, Availability.TAKEN),
            (source.verified_free, Availability.AVAILABLE),
        ):
            name, _, rest = probe.partition(".")
            suffix = rest if rest != tld else tld
            result = client.check_availability(DomainHack.from_sld(name, TLD(suffix)))
            assert result.availability is expected, (probe, result.error_message)
