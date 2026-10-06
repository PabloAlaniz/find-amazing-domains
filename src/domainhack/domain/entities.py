from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import idna

from domainhack.domain.label_rules import DNS_MAX_LABEL_LENGTH, label_rule_for

_LDH_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_ACE_PREFIX = "xn--"


class InvalidLabelError(ValueError):
    """A candidate SLD is not a label the TLD's registry could hold."""


@dataclass(frozen=True)
class TLD:
    """A top-level domain like 'to', 'in', 'io'."""

    suffix: str

    def __post_init__(self) -> None:
        if not (self.suffix.isascii() and self.suffix.isalpha()) or len(self.suffix) < 2:
            raise ValueError(f"Invalid TLD suffix: {self.suffix!r}")


def _check_ldh(ascii_label: str, original: str) -> None:
    """RFC 5891 §4.2.3.1 / RFC 1123 syntax for an ASCII label (any length)."""
    if not _LDH_LABEL.match(ascii_label):
        raise InvalidLabelError(
            f"{original!r} is not a valid label (letters, digits and inner hyphens only)"
        )
    if ascii_label[2:4] == "--" and not ascii_label.startswith(_ACE_PREFIX):
        raise InvalidLabelError(f"{original!r} has '--' in positions 3-4 (reserved)")


def to_ascii_label(label: str, tld: TLD) -> str:
    """Validate ``label`` for ``tld`` and return its ASCII (A-label) form.

    Unicode labels are NFC-normalized and converted with IDNA2008 (the
    ``idna`` package); ``xn--`` input must be a valid A-label. IDN labels are
    only accepted when the TLD's registry supports IDN (see ``label_rules``).
    Raises InvalidLabelError otherwise.
    """
    rule = label_rule_for(tld.suffix)
    unicode_label = unicodedata.normalize("NFC", label)
    if unicode_label.isascii():
        ascii_label = unicode_label
        _check_ldh(ascii_label, label)
        is_idn = ascii_label.startswith(_ACE_PREFIX)
        if is_idn:
            try:
                unicode_label = idna.ulabel(ascii_label)
                if idna.alabel(unicode_label).decode("ascii") != ascii_label:
                    raise idna.IDNAError("not in canonical form")
            except (idna.IDNAError, UnicodeError) as exc:
                raise InvalidLabelError(f"{label!r} is not a valid A-label: {exc}") from exc
    else:
        is_idn = True
        if not rule.idn:
            raise InvalidLabelError(
                f"{label!r} is an internationalized name and .{tld.suffix} does not accept IDN"
            )
        try:
            ascii_label = idna.alabel(unicode_label).decode("ascii")
        except (idna.IDNAError, UnicodeError) as exc:
            raise InvalidLabelError(f"{label!r} is not a valid IDN label: {exc}") from exc
        _check_ldh(ascii_label, label)

    if is_idn and not rule.idn:
        raise InvalidLabelError(
            f"{label!r} is an internationalized name and .{tld.suffix} does not accept IDN"
        )
    if not is_idn and ascii_label.startswith(rule.forbidden_prefixes):
        raise InvalidLabelError(f".{tld.suffix} does not accept names like {label!r}")
    if len(unicode_label) < rule.min_length:
        raise InvalidLabelError(
            f".{tld.suffix} requires at least {rule.min_length} characters, got {label!r}"
        )
    if len(ascii_label) > min(rule.max_length, DNS_MAX_LABEL_LENGTH):
        raise InvalidLabelError(f"{label!r} is too long for .{tld.suffix}")
    return ascii_label


@dataclass(frozen=True)
class DomainHack:
    """A word that ends with a TLD suffix, split into SLD + TLD.

    Example: word='plato', tld=TLD('to') -> sld='pla', fqdn='pla.to'

    Naming convention for internationalized names: ``word`` and ``sld`` keep
    what the user wrote (lowercased, NFC), ``display`` is ``sld.tld`` in that
    form, and ``fqdn``/``ascii_sld`` are **always ASCII** (the IDNA A-label).
    Everything that talks to a registry or keys a cache uses ``fqdn``; only
    human-facing output uses ``display``. For ASCII names both are equal.

    Construction validates the label (LDH syntax plus the TLD's rules from
    ``label_rules``) and raises InvalidLabelError, so an invalid name can
    never reach a registrar adapter.
    """

    word: str
    sld: str
    tld: TLD
    ascii_sld: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ascii_sld", to_ascii_label(self.sld, self.tld))

    @property
    def fqdn(self) -> str:
        """The ASCII name used for every query and cache key (A-label for IDNs)."""
        return f"{self.ascii_sld}.{self.tld.suffix}"

    @property
    def display(self) -> str:
        """The name as the user wrote it (U-label for IDNs), for display only."""
        return f"{self.sld}.{self.tld.suffix}"

    @property
    def is_idn(self) -> bool:
        return self.fqdn != self.display

    @staticmethod
    def _normalize(text: str) -> str:
        return unicodedata.normalize("NFC", text.strip().lower())

    @staticmethod
    def from_word(word: str, tld: TLD) -> DomainHack | None:
        """Split ``word`` into SLD + ``tld``.

        Returns None when the word does not end with the TLD suffix or leaves
        an empty SLD; raises InvalidLabelError when it does but the SLD is
        not a valid label for that TLD.
        """
        lower = DomainHack._normalize(word)
        if lower.endswith(tld.suffix) and len(lower) > len(tld.suffix):
            sld = lower[: -len(tld.suffix)]
            return DomainHack(word=lower, sld=sld, tld=tld)
        return None

    @staticmethod
    def from_sld(sld: str, tld: TLD) -> DomainHack:
        """Create a DomainHack directly from an SLD (for brute-force range mode).

        Raises InvalidLabelError when ``sld`` is not a valid label for ``tld``.
        """
        lower = DomainHack._normalize(sld)
        return DomainHack(word=f"{lower}{tld.suffix}", sld=lower, tld=tld)


class Availability(Enum):
    AVAILABLE = "available"
    TAKEN = "taken"
    ERROR = "error"


# RFC 9083 §10.2.2 status values (RFC 8056 maps the EPP and RGP codes to
# them) that mean the registration is on its way out: after "redemption
# period" (about 30 days, the holder can still restore it) comes "pending
# delete" (about 5 days, nobody can), then the name is released.
# "pending restore" is left out on purpose: it means the holder asked to
# restore the name during redemption, so it is most likely coming back.
DROPPING_STATUSES: frozenset[str] = frozenset({"pending delete", "redemption period"})


@dataclass(frozen=True)
class DnsEvidence:
    """What public DNS says about a name: a second opinion, never the verdict.

    ``nameservers`` are the delegated NS hosts (lowercase, no trailing dot);
    ``has_address`` is True when the name resolves to an A or AAAA record.
    ``error`` is set when the lookup itself failed (timeout, SERVFAIL...), in
    which case the other fields carry no information.
    """

    nameservers: tuple[str, ...] = ()
    has_address: bool = False
    error: str = ""

    @property
    def is_delegated(self) -> bool:
        """True when the name has NS records, i.e. it is certainly registered."""
        return not self.error and bool(self.nameservers)


@dataclass(frozen=True)
class DomainCheckResult:
    """Result of checking a single domain's availability.

    Registration details a registry may return for TAKEN names, all empty
    when unknown:

    - ``statuses``: RFC 9083 status values (lowercase words, e.g.
      ``"client hold"``, ``"pending delete"``);
    - ``expires_at`` / ``registered_at``: aware UTC datetimes;
    - ``registrar``: the sponsoring registrar's name;
    - ``nameservers``: delegated NS hosts as published by the registry.

    ``dns`` is optional DNS evidence gathered after the registry check (see
    ``DnsEvidence``); ``parked_hint`` names the parking/aftermarket service
    the nameservers point to (e.g. ``"domainrecover"``), when recognised.
    """

    domain: DomainHack
    availability: Availability
    raw_title: str = ""
    error_message: str = ""
    statuses: tuple[str, ...] = ()
    expires_at: datetime | None = None
    registered_at: datetime | None = None
    registrar: str = ""
    nameservers: tuple[str, ...] = ()
    parked_hint: str = ""
    dns: DnsEvidence | None = None

    @property
    def dns_conflict(self) -> bool:
        """AVAILABLE per the registry, yet delegated in DNS: do not trust it as free."""
        return (
            self.availability is Availability.AVAILABLE
            and self.dns is not None
            and self.dns.is_delegated
        )

    @property
    def dropping_statuses(self) -> tuple[str, ...]:
        """The statuses in ``DROPPING_STATUSES``, in registry order."""
        return tuple(s for s in self.statuses if s in DROPPING_STATUSES)

    @property
    def is_dropping(self) -> bool:
        """True for a TAKEN name in redemption or pending delete (it may soon be free)."""
        return self.availability is Availability.TAKEN and bool(self.dropping_statuses)
