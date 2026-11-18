"""tools/weekly_report.py renders the weekly report from `check` JSON Lines output."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
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


# ── changes since the previous run, history and feed ─────


def _main(tmp_path: Path, results: list[dict[str, Any]], date: str) -> Path:
    path = tmp_path / "results.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in results), encoding="utf-8")
    words = tmp_path / "words.txt"
    words.write_text("plato\n", encoding="utf-8")
    out = tmp_path / "reports"
    argv = [str(path), "--words", str(words), "--tlds", "3", "--out", str(out), "--date", date]
    assert weekly_report.main(argv) == 0
    return out


NEXT_WEEK = [
    _record("pla.to", "plato", "taken"),  # was available: registered
    _record("can.to", "canto", "available"),  # still available
    _record("ve.to", "veto", "available"),  # was taken: newly available
    _record("gus.to", "gusto", "taken", statuses=["pending delete"]),  # still dropping
    _record("abas.to", "abasto", "taken", statuses=["redemption period"]),  # newly dropping
    _record("test.ing", "testing", "error", error_message="boom"),
    _record("ama.zing", "amazing", "available", dns_conflict=True),
]


def test_snapshot_lists_available_and_dropping_names() -> None:
    assert weekly_report.snapshot(RESULTS, dt.date(2026, 11, 3)) == {
        "date": "2026-11-03",
        "available": ["can.to", "pla.to"],
        "dropping": ["gus.to"],
    }


def test_changes_since_the_previous_run() -> None:
    previous = weekly_report.snapshot(RESULTS, dt.date(2026, 11, 3))
    changes = weekly_report.changes_since(previous, NEXT_WEEK)
    assert changes is not None
    assert changes.since == "2026-11-03"
    assert [r["fqdn"] for r in changes.new_available] == ["ve.to"]
    assert [r["fqdn"] for r in changes.registered] == ["pla.to"]
    assert [r["fqdn"] for r in changes.new_dropping] == ["abas.to"]


def test_a_failed_lookup_is_neither_new_nor_registered() -> None:
    previous = {"date": "2026-11-03", "available": ["test.ing"], "dropping": []}
    changes = weekly_report.changes_since(previous, NEXT_WEEK)
    assert changes is not None
    assert "test.ing" not in [r["fqdn"] for r in changes.registered]


def test_no_previous_run_no_changes_section() -> None:
    assert weekly_report.changes_since(None, RESULTS) is None
    text = weekly_report.render(RESULTS, words=1, tlds=1, date=dt.date(2026, 11, 3), command="x")
    assert "## Changes since" not in text


def test_changes_section_comes_before_the_lists() -> None:
    previous = weekly_report.snapshot(RESULTS, dt.date(2026, 11, 3))
    changes = weekly_report.changes_since(previous, NEXT_WEEK)
    text = weekly_report.render(
        NEXT_WEEK, words=1, tlds=1, date=dt.date(2026, 11, 10), command="x", changes=changes
    )
    assert text.index("## Changes since 2026-11-03") < text.index("## Available now (2)")
    assert "1 newly available, 1 registered by someone, 1 newly dropping." in text
    assert "### Newly available (1)\n\n| Domain | Word |\n|---|---|\n| ve.to | veto |" in text
    assert "### Registered since then (1)" in text
    assert "### Newly dropping (1)" in text


def test_unchanged_week_has_counts_but_no_tables() -> None:
    previous = weekly_report.snapshot(RESULTS, dt.date(2026, 11, 3))
    changes = weekly_report.changes_since(previous, RESULTS)
    text = weekly_report.render(
        RESULTS, words=1, tlds=1, date=dt.date(2026, 11, 10), command="x", changes=changes
    )
    assert "0 newly available, 0 registered by someone, 0 newly dropping." in text
    assert "### Newly available" not in text


def test_two_runs_write_snapshot_history_and_feed(tmp_path: Path) -> None:
    out = _main(tmp_path, RESULTS, "2026-11-03")
    assert "## Changes since" not in (out / "latest.md").read_text(encoding="utf-8")
    assert json.loads((out / "latest.json").read_text())["available"] == ["can.to", "pla.to"]
    (first,) = json.loads((out / "history.json").read_text())
    assert (first["report"], first["available"], first["new_available"]) == ("2026-W45.md", 2, None)

    _main(tmp_path, NEXT_WEEK, "2026-11-10")
    latest = (out / "latest.md").read_text(encoding="utf-8")
    assert "## Changes since 2026-11-03" in latest
    assert "## Changes since" not in (out / "2026-W45.md").read_text(encoding="utf-8")
    assert json.loads((out / "latest.json").read_text())["date"] == "2026-11-10"
    history = json.loads((out / "history.json").read_text())
    assert [h["date"] for h in history] == ["2026-11-10", "2026-11-03"]
    assert history[0]["new_available_names"] == ["ve.to"]
    assert history[0]["dropping_names"] == ["gus.to", "abas.to"]

    feed = ET.fromstring((out / "feed.xml").read_text(encoding="utf-8"))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    assert feed.findtext("a:updated", namespaces=ns) == "2026-11-10T00:00:00Z"
    entries = feed.findall("a:entry", ns)
    assert [e.findtext("a:title", namespaces=ns) for e in entries] == [
        "Domain hacks, week 46 of 2026: 2 available, 1 new",
        "Domain hacks, week 45 of 2026: 2 available",
    ]
    link = entries[0].find("a:link", ns)
    assert link is not None
    assert link.get("href", "").endswith("/blob/reports/reports/2026-W46.md")
    summary = entries[0].findtext("a:summary", namespaces=ns) or ""
    assert "Since 2026-11-03: 1 newly available, 1 registered by someone." in summary
    assert "Newly available: ve.to." in summary
    assert len({e.findtext("a:id", namespaces=ns) for e in entries}) == 2


def test_rerun_for_the_same_date_replaces_its_entry_and_compares_with_nothing(
    tmp_path: Path,
) -> None:
    out = _main(tmp_path, RESULTS, "2026-11-03")
    _main(tmp_path, NEXT_WEEK, "2026-11-03")
    assert "## Changes since" not in (out / "latest.md").read_text(encoding="utf-8")
    history = json.loads((out / "history.json").read_text())
    assert len(history) == 1
    assert json.loads((out / "latest.json").read_text())["available"] == ["can.to", "ve.to"]


def test_an_older_report_does_not_replace_a_newer_snapshot(tmp_path: Path) -> None:
    out = _main(tmp_path, NEXT_WEEK, "2026-11-10")
    _main(tmp_path, RESULTS, "2026-11-03")
    assert json.loads((out / "latest.json").read_text())["date"] == "2026-11-10"
    history = json.loads((out / "history.json").read_text())
    assert [h["date"] for h in history] == ["2026-11-10", "2026-11-03"]


@pytest.mark.parametrize(
    "text", ["nope", "[]", "{}", '{"date": "x", "available": [], "dropping": []}']
)
def test_an_unusable_snapshot_is_ignored_with_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    out = tmp_path / "reports"
    out.mkdir()
    (out / "latest.json").write_text(text)
    (out / "history.json").write_text("{}")
    _main(tmp_path, RESULTS, "2026-11-03")
    err = capsys.readouterr().err
    assert "warning: ignoring" in err
    assert json.loads((out / "latest.json").read_text())["date"] == "2026-11-03"
    assert len(json.loads((out / "history.json").read_text())) == 1


def test_history_is_capped() -> None:
    history: list[dict[str, Any]] = []
    start = dt.date(2026, 1, 6)
    for week in range(weekly_report.HISTORY_LIMIT + 4):
        entry = weekly_report.history_entry([], start + dt.timedelta(weeks=week), None)
        history = weekly_report.update_history(history, entry)
    assert len(history) == weekly_report.HISTORY_LIMIT
    assert history[0]["date"] > history[-1]["date"]


def test_feed_lists_only_the_first_names() -> None:
    results = [_record(f"w{i:03d}.to", f"w{i:03d}to", "available") for i in range(60)]
    changes = weekly_report.changes_since(
        {"date": "2026-11-03", "available": [], "dropping": []}, results
    )
    entry = weekly_report.history_entry(results, dt.date(2026, 11, 10), changes)
    assert len(entry["new_available_names"]) == weekly_report.FEED_NAMES_LIMIT
    feed = weekly_report.render_feed([entry])
    assert "w049.to and 10 more." in feed
    assert weekly_report.render_feed([]).count("<entry>") == 0


# ── available.json, read by the documentation site's "Browse" page ───────────


def test_word_languages_come_from_the_list_names(tmp_path: Path) -> None:
    en = tmp_path / "en-common.txt"
    en.write_text("Veto\ngusto\n\n", encoding="utf-8")
    es = tmp_path / "es-common.txt"
    es.write_text("plato\ngusto\n", encoding="utf-8")
    assert weekly_report.word_languages([es, en]) == {
        "veto": ["en"],
        "gusto": ["en", "es"],
        "plato": ["es"],
    }


def test_browse_data_lists_available_and_dropping_best_first() -> None:
    data = weekly_report.browse_data(
        [*NEXT_WEEK, _record("pla.net", "planet", "available")],
        dt.date(2026, 11, 10),
        {"veto": ["en", "es"], "gusto": ["en"]},
    )
    assert data["date"] == "2026-11-10"
    assert data["available"] == [
        {"domain": "ve.to", "word": "veto", "tld": "to", "langs": ["en", "es"]},
        {"domain": "can.to", "word": "canto", "tld": "to", "langs": []},
        {"domain": "pla.net", "word": "planet", "tld": "net", "langs": []},
    ]
    assert data["dropping"] == [
        {
            "domain": "gus.to",
            "word": "gusto",
            "tld": "to",
            "langs": ["en"],
            "status": "pending delete",
            "expires": None,
        },
        {
            "domain": "abas.to",
            "word": "abasto",
            "tld": "to",
            "langs": [],
            "status": "redemption period",
            "expires": None,
        },
    ]


def test_main_writes_available_json(tmp_path: Path) -> None:
    out = _main(tmp_path, RESULTS, "2026-11-03")
    data = json.loads((out / "available.json").read_text(encoding="utf-8"))
    assert [item["domain"] for item in data["available"]] == ["can.to", "pla.to"]
    assert data["available"][1]["langs"] == ["words"]  # the test list is words.txt
    assert data["dropping"][0]["expires"] == "2026-10-01"
    # An older report does not replace the newer data.
    _main(tmp_path, NEXT_WEEK, "2026-10-27")
    assert json.loads((out / "available.json").read_text())["date"] == "2026-11-03"
