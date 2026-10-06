"""Per-TLD registration rules for the label left of the TLD (the SLD).

These rules decide whether a candidate is worth querying at all. A query for
a name the registry would never accept can only produce noise, and worse, a
"not found" answer that reads as AVAILABLE. When unsure, an entry is left at
the RFC defaults below and ``idn`` stays False: skipping a candidate costs
less than reporting a false AVAILABLE.

Lengths are counted in characters of the name as the user sees it (the
U-label for IDNs); the DNS limit of 63 octets is checked on the ASCII form.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

DNS_MAX_LABEL_LENGTH = 63


@dataclass(frozen=True)
class LabelRule:
    """What a registry accepts as the label under one TLD.

    ``min_length``: fewest characters in the (Unicode) label.
    ``max_length``: most characters in the ASCII (A-label) form.
    ``idn``: True if the registry registers internationalized names. When
    False, non-ASCII candidates (and ``xn--`` A-labels) are skipped instead
    of being converted, since the registry would never hold them.
    ``forbidden_prefixes``: ASCII prefixes the registry refuses for non-IDN
    labels (e.g. ``.it`` refuses names starting with ``xn``).
    """

    min_length: int = 1
    max_length: int = DNS_MAX_LABEL_LENGTH
    idn: bool = False
    forbidden_prefixes: tuple[str, ...] = ()


# RFC 1035 / RFC 5891 defaults: 1-63 LDH characters, no IDN assumed.
DEFAULT_LABEL_RULE = LabelRule()

# Conservative entries for the TLDs in the registrar catalog. Only facts
# backed by the cited source are encoded; every other catalog TLD
# (io, sh, ac, me, so, ws, am, gg, im, ma, mx, pe, st, fm, re, tv, ly, is, in,
# ar, ai, ...) uses DEFAULT_LABEL_RULE until a registry source is checked.
TLD_LABEL_RULES: Mapping[str, LabelRule] = {
    # nic.it, "How to register": "A .it domain name can be composed of a
    # minimum of 3 characters and a maximum of 63 ... It should not begin and
    # end with the symbol '-' or start with the character sequence 'xn'", and
    # non-ASCII characters from the Technical Guidelines charset are allowed.
    # https://www.nic.it/en/find-your-it/how-register
    "it": LabelRule(min_length=3, idn=True, forbidden_prefixes=("xn",)),
    # SWITCH FAQ: "Domain names under .ch and .li can also contain non-ASCII
    # characters such as umlauts and accents"; a name "must be at least three
    # characters long". https://www.nic.ch/faqs/idn/
    "ch": LabelRule(min_length=3, idn=True),
    "li": LabelRule(min_length=3, idn=True),
    # DENIC: IDN domains with umlauts, accents and 93 additional characters
    # (IDNA2008). https://www.denic.de/en/know-how/idn-domains/
    "de": LabelRule(idn=True),
    # Registries with a Latin IDN table published in the IANA Repository of
    # IDN Practices, https://www.iana.org/domains/idn-tables
    # (at_latn_1.0, be_latn_1.1, co_es_3.0 et al., la_latn_1.0, nu_und-latn_2).
    "at": LabelRule(idn=True),
    "be": LabelRule(idn=True),
    "co": LabelRule(idn=True),
    "la": LabelRule(idn=True),
    "nu": LabelRule(idn=True),
    # 101domain .to page: ".to Domain Names must have minimum of 2 and a
    # maximum of 61 characters", "use the English character set" (languages
    # supported: none). https://www.101domain.com/to-information-help.htm
    # min_length stays 1 on purpose: 1-character names exist (a.to is
    # registered), other registrar sources say 2 or 3, and Tonic's RDAP answers
    # 200 (TAKEN) for registry-held names, so a short query cannot produce a
    # false AVAILABLE.
    "to": LabelRule(max_length=61, idn=False),
    # NIC Argentina, second-level zones (com.ar, net.ar, org.ar...): the
    # Reglamento approved by Resolución DNRDI 43/2019, Art. 13, as amended by
    # Resolución SLYT 2/2022: a name has "UNO (1) y CINCUENTA (50)" characters,
    # not counting the zone (1-3 character names are registered after NIC.ar
    # reviews them, so a short query is still worth an answer).
    # https://www.boletinoficial.gob.ar/detalleAviso/primera/255797/20220106
    # Valid characters are "las letras de los alfabetos español y portugués
    # (incluidas la 'ñ' y la 'ç'), las vocales acentuadas y con diéresis, los
    # números y el guión" (Resolución 110/2016, Art. 9).
    # https://www.boletinoficial.gob.ar/detalleAviso/primera/148316/20160720
    # Other second-level suffixes (com.mx, co.uk...) use DEFAULT_LABEL_RULE
    # until their registry's rules are checked.
    **dict.fromkeys(("com.ar", "net.ar", "org.ar"), LabelRule(max_length=50, idn=True)),
}


def label_rule_for(suffix: str) -> LabelRule:
    """The rule for ``suffix`` (case-insensitive), or the RFC default."""
    return TLD_LABEL_RULES.get(suffix.lower().lstrip("."), DEFAULT_LABEL_RULE)
