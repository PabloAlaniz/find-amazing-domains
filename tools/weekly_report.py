"""Render the weekly domain-hacks report from ``domainhack check --format json`` output.

    python tools/weekly_report.py results.jsonl --words data/wordlists/*.txt \
        --tlds 170 --out reports

Writes ``reports/latest.md`` and ``reports/<year>-W<week>.md`` for the ISO week
of ``--date`` (default: today). The weekly-report workflow runs it after
checking the bundled word lists against every TLD domainhack can query.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from domainhack.domain.entities import DROPPING_STATUSES

Record = dict[str, Any]

CAVEAT = (
    '"Available" means the registry reported no registration when asked. Premium and '
    "reserved names can still show as available, and a registrar may price them "
    "differently: confirm there before buying."
)


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
) -> str:
    """The Markdown report for one run."""
    year, week, _ = date.isocalendar()
    available = sorted(
        (r for r in results if r["availability"] == "available" and not r["dns_conflict"]),
        key=_order,
    )
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
    text = render(
        results,
        words=count_words(args.words),
        tlds=args.tlds,
        date=args.date,
        command=args.command,
    )
    year, week, _ = args.date.isocalendar()
    args.out.mkdir(parents=True, exist_ok=True)
    dated = args.out / f"{year}-W{week:02d}.md"
    for path in (dated, args.out / "latest.md"):
        path.write_text(text, encoding="utf-8")
    print(f"wrote {dated} and {args.out / 'latest.md'} ({len(results)} results)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
