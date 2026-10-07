"""Every WHOIS server in registry_sources.json, against its real recorded replies.

The fixtures in tests/fixtures/whois/ are live replies captured on 2026-10-07
for the probes recorded in the data (``verified.taken_probe`` /
``free_probe``). A wrong ``not_found`` pattern would turn registered names
into false "available" results, so each server must read its taken reply as
TAKEN and its free reply as AVAILABLE.
"""

from pathlib import Path

import pytest

from domainhack.adapters.registry_sources import load_registry_sources
from domainhack.domain.entities import TLD, Availability, DomainHack
from tests.test_whois_registrar import _client

FIXTURES = Path(__file__).parent / "fixtures" / "whois"


def _cases() -> list[tuple[str, str, str, Availability]]:
    cases = []
    for tld, source in sorted(load_registry_sources().tlds.items()):
        if source.status != "whois" or not (FIXTURES / f"{tld}.free.txt").exists():
            continue
        cases.append((tld, "taken", source.verified_taken, Availability.TAKEN))
        cases.append((tld, "free", source.verified_free, Availability.AVAILABLE))
    return cases


def _hack(fqdn: str, tld: str) -> DomainHack:
    name = fqdn[: -len(tld) - 1]
    if "." in name:  # second-level probe such as google.com.kw
        sld, middle = name.split(".", 1)
        return DomainHack.from_sld(sld, TLD(f"{middle}.{tld}"))
    return DomainHack.from_sld(name, TLD(tld))


CASES = _cases()


def test_every_new_whois_server_has_fixtures() -> None:
    sources = load_registry_sources()
    recorded = {tld for tld, *_ in CASES}
    new = {
        t for t, s in sources.tlds.items() if s.status == "whois" and s.verified_on == "2026-10-07"
    }
    assert new <= recorded, f"missing fixtures for {sorted(new - recorded)}"


@pytest.mark.parametrize(("tld", "kind", "probe", "expected"), CASES, ids=lambda v: str(v))
def test_recorded_reply_is_read_correctly(
    tld: str, kind: str, probe: str, expected: Availability
) -> None:
    reply = (FIXTURES / f"{tld}.{kind}.txt").read_bytes()
    client, _connector, _clock = _client(reply)
    result = client.check_availability(_hack(probe, tld))
    assert result.availability is expected, (tld, kind, result.error_message)
