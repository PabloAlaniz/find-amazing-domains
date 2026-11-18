"""Render the weekly domain-hacks report from ``domainhack check --format json`` output.

    python tools/weekly_report.py results.jsonl --words data/wordlists/*.txt \
        --tlds 170 --out reports

Writes ``reports/latest.md`` and ``reports/<year>-W<week>.md`` for the ISO week
of ``--date`` (default: today). The weekly-report workflow runs it after
checking the bundled word lists against every TLD domainhack can query.

When ``--out`` already holds the previous run's files, the report also says
what changed since then (names that became available, names that were
registered) and three more files are kept up to date:

- ``latest.json``: the names available and dropping in this run, which the
  next run compares itself with;
- ``history.json``: one summary per run, newest first (the last 26);
- ``feed.xml``: an Atom feed with one entry per run, built from the history.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domainhack.domain.entities import DROPPING_STATUSES

Record = dict[str, Any]

CAVEAT = (
    '"Available" means the registry reported no registration when asked. Premium and '
    "reserved names can still show as available, and a registrar may price them "
    "differently: confirm there before buying. Several country-code registries accept "
    "registrations only from local residents or companies; a TLD where almost every name "
    "shows as available is usually one of them."
)


REPO_URL = "https://github.com/PabloAlaniz/find-amazing-domains"
REPORTS_URL = f"{REPO_URL}/blob/reports/reports"
FEED_URL = (
    "https://raw.githubusercontent.com/PabloAlaniz/find-amazing-domains/reports/reports/feed.xml"
)
HISTORY_LIMIT = 26
# Names listed in a feed entry and in a history record; the report has them all.
FEED_NAMES_LIMIT = 50
ATOM = "http://www.w3.org/2005/Atom"


def _is_available(record: Record) -> bool:
    return record["availability"] == "available" and not record["dns_conflict"]


def snapshot(results: list[Record], date: dt.date) -> dict[str, Any]:
    """What the next run needs to tell what changed: the available and dropping names."""
    return {
        "date": date.isoformat(),
        "available": sorted(r["fqdn"] for r in results if _is_available(r)),
        "dropping": sorted(r["fqdn"] for r in results if _is_dropping(r)),
    }


@dataclass(frozen=True)
class Changes:
    """What changed since the run of ``since``. Every list is in report order."""

    since: str
    new_available: list[Record]
    registered: list[Record]
    new_dropping: list[Record]


def changes_since(previous: dict[str, Any] | None, results: list[Record]) -> Changes | None:
    """Compare this run with the previous run's ``snapshot`` (None: no previous run).

    ``registered`` only has names that were available and are taken now. A
    name whose lookup failed this week, or that is no longer checked, is in
    neither list: nothing is known about it.
    """
    if previous is None:
        return None
    was_available = set(previous["available"])
    was_dropping = set(previous["dropping"])
    return Changes(
        since=str(previous["date"]),
        new_available=sorted(
            (r for r in results if _is_available(r) and r["fqdn"] not in was_available),
            key=_order,
        ),
        registered=sorted(
            (r for r in results if r["availability"] == "taken" and r["fqdn"] in was_available),
            key=_order,
        ),
        new_dropping=sorted(
            (r for r in results if _is_dropping(r) and r["fqdn"] not in was_dropping), key=_order
        ),
    )


def _changes_section(changes: Changes) -> list[str]:
    lines = [
        "",
        f"## Changes since {changes.since}",
        "",
        f"{len(changes.new_available):,} newly available, {len(changes.registered):,} "
        f"registered by someone, {len(changes.new_dropping):,} newly dropping.",
    ]
    for title, records in (
        ("Newly available", changes.new_available),
        ("Registered since then", changes.registered),
        ("Newly dropping", changes.new_dropping),
    ):
        if records:
            lines += ["", f"### {title} ({len(records):,})", ""]
            lines += _table(["Domain", "Word"], [[r["display"], r["word"]] for r in records])
    return lines


def history_entry(results: list[Record], date: dt.date, changes: Changes | None) -> dict[str, Any]:
    """The summary of one run kept in ``history.json`` (and shown in the feed)."""
    year, week, _ = date.isocalendar()

    def names(records: list[Record]) -> list[str]:
        return [r["display"] for r in records[:FEED_NAMES_LIMIT]]

    return {
        "date": date.isoformat(),
        "report": f"{year}-W{week:02d}.md",
        "checked": len(results),
        "available": sum(1 for r in results if _is_available(r)),
        "dropping": sum(1 for r in results if _is_dropping(r)),
        "compared_with": changes.since if changes else None,
        "new_available": len(changes.new_available) if changes else None,
        "registered": len(changes.registered) if changes else None,
        "new_available_names": names(changes.new_available) if changes else [],
        "dropping_names": names([r for r in results if _is_dropping(r)]),
    }


def update_history(history: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """``history`` with ``entry`` first; a run on the same date replaces the earlier one."""
    kept = [h for h in history if h.get("date") != entry["date"]]
    ordered = sorted([entry, *kept], key=lambda h: str(h.get("date")), reverse=True)
    return ordered[:HISTORY_LIMIT]


def _entry_title(entry: dict[str, Any]) -> str:
    year, week, _ = dt.date.fromisoformat(entry["date"]).isocalendar()
    title = f"Domain hacks, week {week} of {year}: {entry['available']:,} available"
    if entry.get("new_available") is not None:
        title += f", {entry['new_available']:,} new"
    return title


def _entry_summary(entry: dict[str, Any]) -> str:
    parts = [
        f"{entry['checked']:,} domain hacks checked at the registries: "
        f"{entry['available']:,} available, {entry['dropping']:,} dropping soon."
    ]
    if entry.get("new_available") is not None:
        parts.append(
            f"Since {entry['compared_with']}: {entry['new_available']:,} newly available, "
            f"{entry['registered']:,} registered by someone."
        )
    for label, key, count in (
        ("Newly available", "new_available_names", entry.get("new_available") or 0),
        ("Dropping", "dropping_names", entry["dropping"]),
    ):
        names = entry.get(key) or []
        if names:
            more = f" and {count - len(names):,} more" if count > len(names) else ""
            parts.append(f"{label}: {', '.join(names)}{more}.")
    return " ".join(parts)


def render_feed(history: list[dict[str, Any]]) -> str:
    """An Atom feed with one entry per run in ``history`` (newest first)."""
    ET.register_namespace("", ATOM)
    feed = ET.Element(f"{{{ATOM}}}feed")

    def add(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
        child = ET.SubElement(parent, f"{{{ATOM}}}{tag}", attrs)
        child.text = text
        return child

    def stamp(date: str) -> str:
        return f"{date}T00:00:00Z"

    add(feed, "title", "domainhack: available domain hacks, weekly")
    add(feed, "subtitle", "Common English and Spanish words that spell a free domain name.")
    add(feed, "id", f"{REPORTS_URL}/latest.md")
    add(feed, "link", href=FEED_URL, rel="self", type="application/atom+xml")
    add(feed, "link", href=f"{REPORTS_URL}/latest.md", rel="alternate", type="text/html")
    add(feed, "updated", stamp(history[0]["date"]) if history else "1970-01-01T00:00:00Z")
    author = add(feed, "author")
    add(author, "name", "domainhack weekly report")
    for item in history:
        url = f"{REPORTS_URL}/{item['report']}"
        entry = add(feed, "entry")
        add(entry, "title", _entry_title(item))
        add(entry, "id", url)
        add(entry, "link", href=url, rel="alternate", type="text/html")
        add(entry, "updated", stamp(item["date"]))
        add(entry, "summary", _entry_summary(item))
    ET.indent(feed)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(feed, encoding="unicode") + "\n"


def read_json(path: Path, kind: type) -> Any:
    """The JSON in ``path`` when it exists and is a ``kind``; otherwise None, with a warning
    for a file that is there but unusable (the report is still written, without it)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        print(f"warning: ignoring {path}: {exc}", file=sys.stderr)
        return None
    if not isinstance(data, kind):
        print(f"warning: ignoring {path}: not a JSON {kind.__name__}", file=sys.stderr)
        return None
    return data


def load_previous(path: Path) -> dict[str, Any] | None:
    """The previous run's snapshot, or None when there is none worth comparing with."""
    data = read_json(path, dict)
    if data is None:
        return None
    try:
        dt.date.fromisoformat(data["date"])
        ok = all(isinstance(data[key], list) for key in ("available", "dropping"))
    except (KeyError, TypeError, ValueError):
        ok = False
    if not ok:
        print(f"warning: ignoring {path}: not a report snapshot", file=sys.stderr)
        return None
    return data


def load_results(path: Path) -> list[Record]:
    """Read the JSON Lines file ``domainhack check --output x.jsonl --format json`` writes."""
    records: list[Record] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                records.append(json.loads(line))
    return records


def count_words(paths: list[Path]) -> int:
    """Distinct non-empty lines across the word lists."""
    words: set[str] = set()
    for path in paths:
        words.update(w for w in path.read_text(encoding="utf-8").split("\n") if w.strip())
    return len(words)


def _is_dropping(record: Record) -> bool:
    return record["availability"] == "taken" and bool(
        DROPPING_STATUSES.intersection(record["statuses"])
    )


def _order(record: Record) -> tuple[int, int, str]:
    return (len(record["sld"]), len(record["word"]), record["fqdn"])


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ") or "-"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(_cell(c) for c in row) + " |" for row in rows)
    return lines


def _date(value: str | None) -> str:
    return value[:10] if value else "-"


def render(
    results: list[Record],
    *,
    words: int,
    tlds: int,
    date: dt.date,
    command: str,
    changes: Changes | None = None,
) -> str:
    """The Markdown report for one run; ``changes`` adds what changed since the last one."""
    year, week, _ = date.isocalendar()
    available = sorted((r for r in results if _is_available(r)), key=_order)
    conflicts = sorted(
        (r for r in results if r["availability"] == "available" and r["dns_conflict"]),
        key=_order,
    )
    taken = [r for r in results if r["availability"] == "taken"]
    dropping = sorted(
        (r for r in taken if _is_dropping(r)), key=lambda r: (r["expires_at"] or "9", r["fqdn"])
    )
    errors = [r for r in results if r["availability"] == "error"]

    lines = [
        f"# Domain hacks: week {week} of {year}",
        "",
        f"Checked on {date.isoformat()}: {len(results):,} domain hacks spelled by {words:,} common "
        f"English and Spanish words, across {tlds} TLDs, asked directly at the registries. "
        f"**{len(available):,} available**, {len(taken):,} taken ({len(dropping):,} of them in "
        f"redemption or pending delete, so they may be free soon), {len(conflicts):,} reported "
        f"available but delegated in DNS, {len(errors):,} errors.",
        "",
        CAVEAT,
        *(_changes_section(changes) if changes is not None else []),
        "",
        f"## Available now ({len(available):,})",
        "",
    ]
    if available:
        lines += _table(["Domain", "Word"], [[r["display"], r["word"]] for r in available])
    else:
        lines.append("None this week.")

    lines += ["", f"## Dropping soon ({len(dropping):,})", ""]
    if dropping:
        lines.append(
            "Registered names in redemption period or pending delete. The holder can still "
            "restore a name in redemption; pending delete usually frees it within five days."
        )
        lines.append("")
        lines += _table(
            ["Domain", "Word", "Status", "Expires"],
            [
                [
                    r["display"],
                    r["word"],
                    ", ".join(s for s in r["statuses"] if s in DROPPING_STATUSES),
                    _date(r["expires_at"]),
                ]
                for r in dropping
            ],
        )
    else:
        lines.append("None this week.")

    lines += ["", f"## Available at the registry, but delegated in DNS ({len(conflicts):,})", ""]
    if conflicts:
        lines.append(
            'The registry answered "not registered" while public DNS returns nameservers. '
            "Treat these as not free until a registrar confirms."
        )
        lines.append("")
        lines += _table(
            ["Domain", "Word", "Nameservers"],
            [[r["display"], r["word"], ", ".join(r["dns_nameservers"])] for r in conflicts],
        )
    else:
        lines.append("None this week.")

    lines += ["", "## By TLD", ""]
    per_tld: dict[str, Counter[str]] = defaultdict(Counter)
    for r in results:
        bucket = per_tld[r["tld"]]
        bucket["checked"] += 1
        if r["availability"] == "available" and not r["dns_conflict"]:
            bucket["available"] += 1
        elif r["availability"] == "taken":
            bucket["taken"] += 1
            if _is_dropping(r):
                bucket["dropping"] += 1
        elif r["availability"] == "error":
            bucket["errors"] += 1
    ordered = sorted(
        per_tld.items(), key=lambda kv: (-kv[1]["available"], -kv[1]["checked"], kv[0])
    )
    lines += _table(
        ["TLD", "Checked", "Available", "Taken", "Dropping", "Errors"],
        [
            [
                f".{tld}",
                str(c["checked"]),
                str(c["available"]),
                str(c["taken"]),
                str(c["dropping"]),
                str(c["errors"]),
            ]
            for tld, c in ordered
        ],
    )

    if errors:
        by_tld: dict[str, list[Record]] = defaultdict(list)
        for r in errors:
            by_tld[r["tld"]].append(r)
        lines += ["", f"## Errors ({len(errors):,})", ""]
        lines.append(
            "Lookups that did not complete (registry down, rate limit reached, circuit breaker "
            "open). They are retried next week."
        )
        lines.append("")
        lines += _table(
            ["TLD", "Domains", "Example"],
            [
                [f".{tld}", str(len(rs)), rs[0]["error_message"]]
                for tld, rs in sorted(by_tld.items())
            ],
        )

    lines += [
        "",
        "## How this report is made",
        "",
        "A GitHub Actions workflow runs every week:",
        "",
        "```bash",
        command,
        "```",
        "",
        "Word lists: `data/wordlists/`. One request in flight per registry host, one second "
        "apart, RFC 7480 backoff and a per-host circuit breaker; results are cached between "
        "runs so taken names are not asked again until they could have expired. TLDs whose "
        "registries forbid automated lookups are never queried (`domainhack tlds --unsupported`).",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("results", type=Path, help="JSON Lines file written by `domainhack check`")
    parser.add_argument(
        "--words", type=Path, nargs="+", required=True, help="word list files that were checked"
    )
    parser.add_argument("--tlds", type=int, required=True, help="number of TLDs that were checked")
    parser.add_argument(
        "--out", type=Path, default=Path("reports"), help="output directory (default: reports)"
    )
    parser.add_argument(
        "--date", type=dt.date.fromisoformat, default=dt.date.today(), help="run date, ISO 8601"
    )
    parser.add_argument(
        "--command",
        default=(
            "domainhack --tld <every checkable TLD> check --file words.txt "
            "--parallel 4 --confirm-dns --output results.jsonl"
        ),
        help="command line shown in the report",
    )
    args = parser.parse_args(argv)

    results = load_results(args.results)
    stored = load_previous(args.out / "latest.json")
    # Compare only with an earlier run: a re-run for the same date has no "since".
    previous = stored if stored is not None and stored["date"] < args.date.isoformat() else None
    changes = changes_since(previous, results)
    text = render(
        results,
        words=count_words(args.words),
        tlds=args.tlds,
        date=args.date,
        command=args.command,
        changes=changes,
    )
    year, week, _ = args.date.isocalendar()
    args.out.mkdir(parents=True, exist_ok=True)
    dated = args.out / f"{year}-W{week:02d}.md"
    for path in (dated, args.out / "latest.md"):
        path.write_text(text, encoding="utf-8")

    history = update_history(
        read_json(args.out / "history.json", list) or [],
        history_entry(results, args.date, changes),
    )
    (args.out / "history.json").write_text(json.dumps(history, indent=1) + "\n", encoding="utf-8")
    (args.out / "feed.xml").write_text(render_feed(history), encoding="utf-8")
    # Written last, so a run that failed above leaves the previous snapshot in place; a
    # report rendered for an older date never replaces a newer snapshot.
    if stored is None or stored["date"] <= args.date.isoformat():
        (args.out / "latest.json").write_text(
            json.dumps(snapshot(results, args.date)) + "\n", encoding="utf-8"
        )
    print(f"wrote {dated} and {args.out / 'latest.md'} ({len(results)} results)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
