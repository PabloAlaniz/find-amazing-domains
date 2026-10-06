"""Recognise parking and aftermarket services from a domain's nameservers.

A taken name delegated to a parking or "for sale" service is usually not in
use: the owner monetises it with ads or waits for a buyer. Knowing that is
what turns "taken" into "taken, but maybe buyable". This module is pure (no
I/O): give it NS host names, from the registry or from DNS, and it names the
service.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

# NS domain -> short provider name. A nameserver matches when it equals the
# domain or is a subdomain of it ("ns1.sedoparking.com" -> "sedo").
#
# Curated 2026-10-06 from:
# - the live RDAP record of sumanda.com (NS1/NS2.DOMAINRECOVER.COM);
# - APNIC blog, "The prevalence of domain parking" (2023-11-08), which
#   identifies Afternic by ns*.afternic.com:
#   https://blog.apnic.net/2023/11/08/the-prevalence-of-domain-parking/
# - GoDaddy CashParking setup docs (ns01/ns02.cashparking.com):
#   https://www.godaddy.com/domains/cashparking
# - Bodis setup docs (ns1/ns2.bodis.com). Bodis closed on 2026-01-31, but
#   registries still list domains delegated to it:
#   https://domaininvesting.com/bodis-to-cease-operating-on-january-31/
# - Nameserver history on dns.coffee for ns1/ns2 of sedoparking.com,
#   parkingcrew.net, dan.com, hugedomains.com, namebrightdns.com,
#   undeveloped.com and above.com, e.g. https://dns.coffee/domains/filmeja.com
# Every host was also checked to resolve (dig) on 2026-10-06, except
# ns1.bodis.com (service closed).
#
# namebrightdns.com is NameBright's general DNS, but names left on it are
# overwhelmingly the DropCatch/TurnCommerce aftermarket portfolio. Plain
# registrar DNS (domaincontrol.com, registrar-servers.com...) is deliberately
# absent: it hosts live sites as often as parked pages.
PARKING_NAMESERVERS: Mapping[str, str] = {
    "domainrecover.com": "domainrecover",
    "sedoparking.com": "sedo",
    "parkingcrew.net": "parkingcrew",
    "bodis.com": "bodis",
    "afternic.com": "afternic",
    "dan.com": "dan.com",
    "hugedomains.com": "hugedomains",
    "cashparking.com": "godaddy-parked",
    "namebrightdns.com": "namebright",
    "undeveloped.com": "undeveloped",
    "above.com": "above",
    "uniregistrymarket.link": "uniregistry",
    "parklogic.com": "parklogic",
}


def _provider_for(host: str) -> str:
    labels = host.split(".")
    # Try every suffix, longest first: "a.b.dan.com" -> "b.dan.com", "dan.com", "com".
    for start in range(len(labels)):
        provider = PARKING_NAMESERVERS.get(".".join(labels[start:]))
        if provider is not None:
            return provider
    return ""


def parking_hint_for(nameservers: Iterable[str]) -> str:
    """The parking/aftermarket provider the nameservers point to, or ``""``.

    Names are compared case-insensitively and a trailing dot is ignored, so
    registry (``NS1.DOMAINRECOVER.COM``) and DNS (``ns1.domainrecover.com.``)
    spellings both work. The first nameserver that matches decides;
    anything that is not a string is skipped.
    """
    for nameserver in nameservers:
        if not isinstance(nameserver, str):
            continue
        host = nameserver.strip().rstrip(".").lower()
        if not host:
            continue
        provider = _provider_for(host)
        if provider:
            return provider
    return ""
