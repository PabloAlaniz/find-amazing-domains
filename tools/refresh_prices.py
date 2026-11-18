"""Refresh the bundled reference prices (``src/domainhack/data/tld_prices.json``).

    python tools/refresh_prices.py            # fetch Porkbun's public price list
    python tools/refresh_prices.py --from-file pricing.json   # or read a saved reply

The source is Porkbun's public pricing endpoint, which needs no account or key
and lists the registration and renewal price of every TLD it sells:
https://porkbun.com/api/json/v3/documentation#Domain%20Pricing

Only TLDs domainhack knows (the bundled IANA list) are kept. Run it before a
release, review the diff and commit the file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from domainhack.adapters._http import identity_headers
from domainhack.adapters.iana_tlds import IanaTldList
from domainhack.adapters.tld_prices import parse_price_list

PRICING_URL = "https://api.porkbun.com/api/json/v3/pricing/get"
SOURCE = "Porkbun public price list"
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1] / "src" / "domainhack" / "data" / "tld_prices.json"
)
COMMENT = (
    "Reference prices per TLD, written by tools/refresh_prices.py. Amounts are strings. "
    "An empty 'prices' object means this build has no price data, and none is shown."
)


def fetch(url: str = PRICING_URL, timeout: float = 30.0) -> Any:
    response = httpx.get(url, headers=identity_headers(), timeout=timeout)
    response.raise_for_status()
    return response.json()


def _amount(value: Any) -> str | None:
    """A price as a plain decimal string (``"11.08"``), or None when it is not one."""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0:
        return None
    return f"{amount:.2f}"


def convert(reply: Any, *, retrieved: dt.date, known: IanaTldList | None = None) -> dict[str, Any]:
    """Porkbun's reply as the bundled file's content. ValueError when it is not a price list."""
    if not isinstance(reply, dict) or not isinstance(reply.get("pricing"), dict):
        raise ValueError("the reply has no 'pricing' object")
    tlds = known if known is not None else IanaTldList()
    prices: dict[str, dict[str, str]] = {}
    for suffix, entry in sorted(reply["pricing"].items()):
        suffix = str(suffix).lower()
        if not isinstance(entry, dict) or not tlds.is_known(suffix):
            continue
        price = {
            key: amount
            for key in ("registration", "renewal")
            if (amount := _amount(entry.get(key))) is not None
        }
        if price:
            prices[suffix] = price
    if not prices:
        raise ValueError("the reply lists no price for a known TLD")
    data = {
        "_comment": COMMENT,
        "source": SOURCE,
        "retrieved": retrieved.isoformat(),
        "currency": "USD",
        "prices": prices,
    }
    parse_price_list(data)  # what ships must load
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--from-file", type=Path, help="read a saved reply instead of fetching")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--date", type=dt.date.fromisoformat, default=dt.date.today(), help="retrieval date"
    )
    args = parser.parse_args(argv)
    try:
        if args.from_file:
            reply = json.loads(args.from_file.read_text(encoding="utf-8"))
        else:
            reply = fetch()
        data = convert(reply, retrieved=args.date)
    except (OSError, ValueError, httpx.HTTPError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    args.output.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(data['prices'])} prices to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
