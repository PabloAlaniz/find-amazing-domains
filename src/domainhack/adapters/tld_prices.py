"""The bundled price list (``data/tld_prices.json``), refreshed by ``tools/refresh_prices.py``.

```json
{"source": "...", "retrieved": "2026-11-18", "currency": "USD",
 "prices": {"com": {"registration": "11.08", "renewal": "11.08"}}}
```

Amounts are strings so they stay exact. ``load_price_list`` returns None when
the file lists no price, so a build without price data simply shows none.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from importlib import resources
from typing import Any

from domainhack.domain.pricing import PriceList, TldPrice

TLD_PRICES_FILE = "data/tld_prices.json"


def _amount(value: Any) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"expected an amount as a string, got {value!r}")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"not an amount: {value!r}") from None
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"not an amount: {value!r}")
    return amount


def parse_price_list(data: Any) -> PriceList | None:
    """Validate a price-list object; None when it lists no price. ValueError when malformed."""
    if not isinstance(data, dict) or not isinstance(data.get("prices"), dict):
        raise ValueError("expected an object with a 'prices' object")
    prices: dict[str, TldPrice] = {}
    for suffix, entry in data["prices"].items():
        if not isinstance(entry, dict):
            raise ValueError(f"price of .{suffix}: expected an object")
        try:
            price = TldPrice(_amount(entry.get("registration")), _amount(entry.get("renewal")))
        except ValueError as exc:
            raise ValueError(f"price of .{suffix}: {exc}") from None
        if price.registration is not None or price.renewal is not None:
            prices[str(suffix).lower()] = price
    if not prices:
        return None
    source, retrieved, currency = data.get("source"), data.get("retrieved"), data.get("currency")
    if not (isinstance(source, str) and source and isinstance(currency, str) and currency):
        raise ValueError("'source' and 'currency' must be non-empty strings")
    if not isinstance(retrieved, str):
        raise ValueError("'retrieved' must be an ISO date")
    date.fromisoformat(retrieved)
    return PriceList(source=source, retrieved=retrieved, currency=currency, prices=prices)


def load_price_list() -> PriceList | None:
    """The bundled price list, or None when this build has no price data."""
    text = resources.files("domainhack").joinpath(TLD_PRICES_FILE).read_text(encoding="utf-8")
    return parse_price_list(json.loads(text))
