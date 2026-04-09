from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class TLD:
    """A top-level domain like 'to', 'in', 'io'."""

    suffix: str

    def __post_init__(self) -> None:
        if not self.suffix.isalpha() or len(self.suffix) < 2:
            raise ValueError(f"Invalid TLD suffix: {self.suffix!r}")


@dataclass(frozen=True)
class DomainHack:
    """A word that ends with a TLD suffix, split into SLD + TLD.

    Example: word='plato', tld=TLD('to') -> sld='pla', fqdn='pla.to'
    """

    word: str
    sld: str
    tld: TLD

    @property
    def fqdn(self) -> str:
        return f"{self.sld}.{self.tld.suffix}"

    @staticmethod
    def from_word(word: str, tld: TLD) -> DomainHack | None:
        """Create a DomainHack if the word ends with the TLD suffix and the SLD is non-empty."""
        lower = word.strip().lower()
        if lower.endswith(tld.suffix) and len(lower) > len(tld.suffix):
            sld = lower[: -len(tld.suffix)]
            return DomainHack(word=lower, sld=sld, tld=tld)
        return None

    @staticmethod
    def from_sld(sld: str, tld: TLD) -> DomainHack:
        """Create a DomainHack directly from an SLD (for brute-force range mode)."""
        lower = sld.strip().lower()
        return DomainHack(word=f"{lower}{tld.suffix}", sld=lower, tld=tld)


class Availability(Enum):
    AVAILABLE = "available"
    TAKEN = "taken"
    ERROR = "error"


@dataclass(frozen=True)
class DomainCheckResult:
    """Result of checking a single domain's availability."""

    domain: DomainHack
    availability: Availability
    raw_title: str = ""
    error_message: str = ""
