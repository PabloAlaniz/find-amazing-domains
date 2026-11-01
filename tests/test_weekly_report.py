"""tools/weekly_report.py renders the weekly report from `check` JSON Lines output."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "weekly_report", Path(__file__).resolve().parents[1] / "tools" / "weekly_report.py"
)
assert _SPEC is not None and _SPEC.loader is not None
weekly_report = importlib.util.module_from_spec(_SPEC)
sys.modules["weekly_report"] = weekly_report
_SPEC.loader.exec_module(weekly_report)


def _record(fqdn: str, word: str, availability: str, **extra: Any) -> dict[str, Any]:
    sld, _, tld = fqdn.partition(".")
    record: dict[str, Any] = {
        "fqdn": fqdn,
        "display": fqdn,
        "word": word,
        "sld": sld,
        "tld": tld,
        "availability": availability,
        "error_message": "",
        "statuses": [],
        "expires_at": None,
        "registered_at": None,
        "registrar": "",
        "nameservers": [],
        "parked_hint": "",
        "dns_nameservers": [],
        "dns_conflict": False,
    }
    record.update(extra)
    return record


RESULTS = [
    _record("pla.to", "plato", "available"),
    _record("can.to", "canto", "available"),
    _record("ve.to", "veto", "taken", statuses=["active"], expires_at="2027-09-30T03:28:03Z"),
    _record(
        "gus.to",
        "gusto",
        "taken",
        statuses=["pending delete", "inactive"],
        expires_at="2026-10-01T00:00:00Z",
    ),
    _record(
        "test.ing", "testing", "available", dns_conflict=True, dns_nameservers=["ns1.example.net"]
    ),
    _record("ama.zing", "amazing", "error", error_message="rate limited | by registry"),
]


def test_render_counts_and_sections() -> None:
    text = weekly_report.render(
        RESULTS, words=5237, tlds=170, date=dt.date(2026, 11, 3), command="domainhack check ..."
    )
    assert text.startswith("# Domain hacks: week 45 of 2026\n")
    assert "6 domain hacks spelled by 5,237 common" in text
    assert "across 170 TLDs" in text
    assert "**2 available**, 2 taken (1 of them" in text
    assert "1 reported available but delegated in DNS, 1 errors" in text
    assert "## Available now (2)" in text
    assert "## Dropping soon (1)" in text
    assert "| gus.to | gusto | pending delete | 2026-10-01 |" in text
    assert "## Available at the registry, but delegated in DNS (1)" in text
    assert "| test.ing | testing | ns1.example.net |" in text
    assert "## Errors (1)" in text
    assert "| .zing | 1 | rate limited \\| by registry |" in text
    assert "```bash\ndomainhack check ...\n```" in text


def test_available_sorted_by_sld_length_then_word_length_then_name() -> None:
    results = [
        _record("plan.et", "planet", "available"),
        _record("ve.to", "veto", "available"),
        _record("ama.to", "amato", "available"),
        _record("can.to", "canto", "available"),
    ]
    text = weekly_report.render(results, words=4, tlds=2, date=dt.date(2026, 11, 3), command="x")
    section = text.split("## Available now (4)")[1].split("## Dropping soon")[0]
    rows = [
        line.split(" | ")[0].strip("| ")
        for line in section.splitlines()
        if line.startswith("| ") and not line.startswith("| Domain")
    ]
    assert rows == ["ve.to", "ama.to", "can.to", "plan.et"]


def test_by_tld_table_orders_by_available_then_checked() -> None:
    text = weekly_report.render(RESULTS, words=6, tlds=3, date=dt.date(2026, 11, 3), command="x")
    section = text.split("## By TLD")[1].split("## Errors")[0]
    rows = [line for line in section.splitlines() if line.startswith("| .")]
    assert rows == [
        "| .to | 4 | 2 | 2 | 1 | 0 |",
        "| .ing | 1 | 0 | 0 | 0 | 0 |",
        "| .zing | 1 | 0 | 0 | 0 | 1 |",
    ]


def test_empty_sections_say_so_and_errors_section_is_omitted() -> None:
    text = weekly_report.render([], words=0, tlds=0, date=dt.date(2026, 1, 1), command="x")
    assert text.count("None this week.") == 3
    assert "## Errors" not in text
    assert "# Domain hacks: week 1 of 2026" in text


def test_main_writes_dated_and_latest_files(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    results.write_text("".join(json.dumps(r) + "\n" for r in RESULTS) + "\n", encoding="utf-8")
    words = tmp_path / "words.txt"
    words.write_text("plato\ncanto\n\nveto\nplato\n", encoding="utf-8")
    out = tmp_path / "reports"

    rc = weekly_report.main(
        [
            str(results),
            "--words",
            str(words),
            "--tlds",
            "3",
            "--out",
            str(out),
            "--date",
            "2026-11-03",
        ]
    )

    assert rc == 0
    latest = (out / "latest.md").read_text(encoding="utf-8")
    assert latest == (out / "2026-W45.md").read_text(encoding="utf-8")
    assert "spelled by 3 common" in latest
    assert "across 3 TLDs" in latest


def test_main_requires_words_and_tlds(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        weekly_report.main([str(tmp_path / "x.jsonl")])
    assert exc.value.code == 2
