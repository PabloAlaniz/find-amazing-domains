"""Reference prices: the bundled list, the refresh tool and the name report."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from domainhack.adapters.tld_prices import load_price_list, parse_price_list
from domainhack.cli.app import EXIT_OK, main
from domainhack.cli.name_cmd import NameServices
from domainhack.domain.entities import Availability
from domainhack.domain.pricing import PriceList, TldPrice
from tests.fakes import FakeCatalog, FakeKnownTlds, ScriptedRegistrar

_SPEC = importlib.util.spec_from_file_location(
    "refresh_prices", Path(__file__).resolve().parents[1] / "tools" / "refresh_prices.py"
)
assert _SPEC is not None and _SPEC.loader is not None
refresh_prices = importlib.util.module_from_spec(_SPEC)
sys.modules["refresh_prices"] = refresh_prices
_SPEC.loader.exec_module(refresh_prices)

PRICES = PriceList(
    source="Example Registrar price list",
    retrieved="2026-11-18",
    prices={
        "com": TldPrice(Decimal("11.08"), Decimal("11.08")),
        "io": TldPrice(Decimal("28.12"), Decimal("51.50")),
        "ai": TldPrice(Decimal("70"), Decimal("70")),
        "app": TldPrice(Decimal("14.93"), Decimal("14.93")),
        "co": TldPrice(Decimal("9.99"), Decimal("26.77")),
    },
)
KNOWN = FakeKnownTlds(["com", "io", "ai", "app", "co", "dev"])


class TestPriceList:
    def test_price_for(self) -> None:
        assert PRICES.price_for(".COM") == TldPrice(Decimal("11.08"), Decimal("11.08"))
        assert PRICES.price_for("dev") is None

    @pytest.mark.parametrize(
        ("price", "text"),
        [
            (TldPrice(Decimal("11.08"), Decimal("11.08")), "$11.08/yr"),
            (TldPrice(Decimal("28.12"), Decimal("51.5")), "$28.12/yr, renews $51.50"),
            (TldPrice(Decimal("70"), None), "$70.00/yr"),
            (TldPrice(None, Decimal("12")), "renews $12.00"),
            (TldPrice(), ""),
        ],
    )
    def test_describe(self, price: TldPrice, text: str) -> None:
        assert PRICES.describe(price) == text

    def test_other_currencies(self) -> None:
        assert PriceList("x", "2026-01-01", "EUR").money(Decimal("9.5")) == "€9.50"
        assert PriceList("x", "2026-01-01", "CAD").money(Decimal("9.5")) == "9.50 CAD"

    def test_caveat_names_source_and_date(self) -> None:
        assert "(Example Registrar price list, 2026-11-18)" in PRICES.caveat


class TestParse:
    def test_empty_list_means_no_price_data(self) -> None:
        assert parse_price_list({"source": "", "retrieved": "", "prices": {}}) is None
        assert parse_price_list({"prices": {"com": {}}}) is None

    def test_valid(self) -> None:
        data = {
            "_comment": "x",
            "source": "S",
            "retrieved": "2026-11-18",
            "currency": "USD",
            "prices": {"COM": {"registration": "11.08", "renewal": "12"}, "xyz": {"renewal": "3"}},
        }
        prices = parse_price_list(data)
        assert prices is not None
        assert prices.price_for("com") == TldPrice(Decimal("11.08"), Decimal("12"))
        assert prices.price_for("xyz") == TldPrice(None, Decimal("3"))

    @pytest.mark.parametrize(
        "data",
        [
            [],
            {},
            {"prices": []},
            {"prices": {"com": "11"}},
            {"prices": {"com": {"registration": 11.08}}},
            {"prices": {"com": {"registration": "eleven"}}},
            {"prices": {"com": {"registration": "-1"}}},
            {"prices": {"com": {"registration": "NaN"}}},
            {"prices": {"com": {"registration": "1"}}, "source": "", "currency": "USD"},
            {"prices": {"com": {"registration": "1"}}, "source": "S", "currency": "USD"},
            {
                "prices": {"com": {"registration": "1"}},
                "source": "S",
                "currency": "USD",
                "retrieved": "yesterday",
            },
        ],
    )
    def test_malformed(self, data: Any) -> None:
        with pytest.raises(ValueError):
            parse_price_list(data)

    def test_the_bundled_file_loads(self) -> None:
        prices = load_price_list()
        if prices is not None:  # a build may ship without price data
            dt.date.fromisoformat(prices.retrieved)
            assert prices.source
            assert prices.price_for("com") is not None


REPLY = {
    "status": "SUCCESS",
    "pricing": {
        "com": {"registration": "11.08", "renewal": "11.08", "transfer": "11.08"},
        "io": {"registration": "28.12", "renewal": "51.5"},
        "ai": {"registration": "1,070.00", "renewal": 70},
        "notatld": {"registration": "1.00", "renewal": "1.00"},
        "dev": {"registration": "free", "renewal": None},
        "app": "n/a",
        "co": {"registration": "0", "renewal": True},
    },
}


class TestRefreshTool:
    def test_convert_keeps_known_tlds_and_real_amounts(self) -> None:
        data = refresh_prices.convert(REPLY, retrieved=dt.date(2026, 11, 18))
        assert data["retrieved"] == "2026-11-18"
        assert data["source"] == "Porkbun public price list"
        assert data["prices"] == {
            "ai": {"registration": "1070.00", "renewal": "70.00"},
            "com": {"registration": "11.08", "renewal": "11.08"},
            "io": {"registration": "28.12", "renewal": "51.50"},
        }
        assert parse_price_list(data) is not None

    @pytest.mark.parametrize("reply", [None, {}, {"pricing": []}, {"pricing": {"notatld": {}}}])
    def test_convert_refuses_what_is_not_a_price_list(self, reply: Any) -> None:
        with pytest.raises(ValueError):
            refresh_prices.convert(reply, retrieved=dt.date(2026, 11, 18))

    def test_main_from_file(self, tmp_path: Path) -> None:
        saved = tmp_path / "reply.json"
        saved.write_text(json.dumps(REPLY))
        out = tmp_path / "prices.json"
        argv = ["--from-file", str(saved), "--output", str(out), "--date", "2026-11-18"]
        assert refresh_prices.main(argv) == 0
        assert parse_price_list(json.loads(out.read_text())) is not None

    def test_main_reports_a_bad_reply_and_writes_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        saved = tmp_path / "reply.json"
        saved.write_text("{}")
        out = tmp_path / "prices.json"
        assert refresh_prices.main(["--from-file", str(saved), "--output", str(out)]) == 1
        assert "error: the reply has no 'pricing' object" in capsys.readouterr().err
        assert not out.exists()

    def test_main_fetches_by_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(refresh_prices, "fetch", lambda: REPLY)
        out = tmp_path / "prices.json"
        assert refresh_prices.main(["--output", str(out)]) == 0
        assert json.loads(out.read_text())["prices"]["com"]["registration"] == "11.08"


def _name(
    *argv: str, prices: PriceList | None = PRICES, script: dict[str, Any] | None = None
) -> int:
    registrar = ScriptedRegistrar(script or {}, default=Availability.AVAILABLE)
    services = NameServices(KNOWN, None, supported_tlds=lambda: (), prices=lambda: prices)
    args = ["name", *argv, "--no-progress", "--no-cache", "--no-subdomain-hacks"]
    return main(args, catalog=FakeCatalog(registrar), services=services)


def _row(out: str, name: str) -> str:
    line = next(ln for ln in out.splitlines() if ln.split()[:1] == [name])
    return " ".join(line.split())


class TestNameReport:
    def test_available_names_show_the_tld_price(self, capsys: pytest.CaptureFixture[str]) -> None:
        script = {"sumanda.com": Availability.TAKEN}
        assert _name("sumanda", "--tlds", "com,io,dev", script=script) == EXIT_OK
        out = capsys.readouterr().out
        assert _row(out, "sumanda.io") == "sumanda.io available $28.12/yr, renews $51.50"
        assert _row(out, "sumanda.dev") == "sumanda.dev available"  # no price listed
        assert "$11.08" not in out  # taken names have no price
        assert "1. sumanda.io exact name, strong TLD .io -- $28.12/yr, renews $51.50" in " ".join(
            out.split()
        )
        assert f"  - {PRICES.caveat}\n" in out

    def test_no_prices_flag_and_no_price_data(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _name("sumanda", "--tlds", "com", "--no-prices") == EXIT_OK
        assert "$" not in capsys.readouterr().out
        assert _name("sumanda", "--tlds", "com", prices=None) == EXIT_OK
        out = capsys.readouterr().out
        assert "$" not in out and "Prices are a reference" not in out

    def test_no_caveat_when_no_price_is_shown(self, capsys: pytest.CaptureFixture[str]) -> None:
        only_ai = PriceList("S", "2026-11-18", prices={"ai": TldPrice(Decimal("70"))})
        assert _name("sumanda", "--tlds", "dev", prices=only_ai) == EXIT_OK
        assert "Prices are a reference" not in capsys.readouterr().out

    def test_cheaper_wins_between_equally_good_names(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # .ai, .io, .co and .app are the same tier and the names the same length.
        argv = ("sumanda", "--tlds", "ai,io,co,app", "--format", "json")
        assert _name(*argv) == EXIT_OK
        data = json.loads(capsys.readouterr().out)
        ranked = [r["domain"] for r in data["recommendations"]]
        # Dearer of registration and renewal: app 14.93, co 26.77, io 51.50, ai 70.
        # sumanda.app is one character longer, so length puts it last.
        assert ranked[:4] == ["sumanda.co", "sumanda.io", "sumanda.ai", "sumanda.app"]
        assert _name(*argv, "--no-prices") == EXIT_OK
        data = json.loads(capsys.readouterr().out)
        assert [r["domain"] for r in data["recommendations"]][:3] == [
            "sumanda.ai",
            "sumanda.co",
            "sumanda.io",
        ]

    def test_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _name("sumanda", "--tlds", "io,dev", "--format", "json") == EXIT_OK
        data = json.loads(capsys.readouterr().out)
        assert data["prices"] == {
            "source": "Example Registrar price list",
            "retrieved": "2026-11-18",
            "currency": "USD",
            "note": PRICES.caveat,
        }
        io, dev = data["exact"]
        assert io["price"] == {"registration": "28.12", "renewal": "51.50"}
        assert "price" not in dev
        assert _name("sumanda", "--tlds", "io", "--format", "json", "--no-prices") == EXIT_OK
        assert json.loads(capsys.readouterr().out)["prices"] is None

    def test_markdown(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _name("sumanda", "--tlds", "io", "--format", "markdown") == EXIT_OK
        out = capsys.readouterr().out
        assert "| `sumanda.io` | available | $28.12/yr, renews $51.50 |" in out
        assert "1. **`sumanda.io`**: exact name, strong TLD .io -- $28.12/yr, renews $51.50" in out
        assert f"- {PRICES.caveat}" in out
