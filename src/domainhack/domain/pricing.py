"""Reference prices per TLD: what a registrar's public price list says a name costs.

A reference only. The list is one registrar's, on one day, for an ordinary
name: premium and reserved names cost more, promotions end, and renewal is
often dearer than the first year. It tells ``.com`` (about ten dollars) from
``.ai`` (about seventy), which is what choosing between available names needs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


@dataclass(frozen=True)
class TldPrice:
    """Yearly prices for an ordinary name under one TLD; None when the list has none."""

    registration: Decimal | None = None
    renewal: Decimal | None = None


@dataclass(frozen=True)
class PriceList:
    """A dated snapshot of a registrar's price list. ``retrieved`` is an ISO date."""

    source: str
    retrieved: str
    currency: str = "USD"
    prices: Mapping[str, TldPrice] = field(default_factory=dict)

    def price_for(self, suffix: str) -> TldPrice | None:
        """The price for ``suffix`` (``"com"``, ``"com.ar"``), or None when not listed."""
        return self.prices.get(suffix.lower().lstrip("."))

    def money(self, amount: Decimal) -> str:
        """``"$11.08"`` (``"11.08 CAD"`` for a currency without a known symbol)."""
        symbol = _SYMBOLS.get(self.currency)
        return f"{symbol}{amount:.2f}" if symbol else f"{amount:.2f} {self.currency}"

    def describe(self, price: TldPrice) -> str:
        """``"$11.08/yr, renews $12.52"``; only the known parts, renewal only when it differs."""
        parts: list[str] = []
        if price.registration is not None:
            parts.append(f"{self.money(price.registration)}/yr")
        if price.renewal is not None and price.renewal != price.registration:
            parts.append(f"renews {self.money(price.renewal)}")
        return ", ".join(parts)

    @property
    def caveat(self) -> str:
        return (
            f"Prices are a reference ({self.source}, {self.retrieved}) for an ordinary "
            "name; premium and reserved names cost more. Confirm at a registrar."
        )
