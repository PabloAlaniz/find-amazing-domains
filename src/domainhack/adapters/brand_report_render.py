"""Render a ``BrandReport`` as text (aligned, for the terminal), Markdown or JSON.

Every renderer is a pure function of the report, so output is deterministic.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

from domainhack.ports.handle_checker import HandleResult, HandleStatus
from domainhack.usecases.brand_report import BrandReport, ReportEntry

Renderer = Callable[[BrandReport], str]

_NONE = "(none)"
_NO_VARIANTS = "not requested (add --variants)"


def _variants_empty(report: BrandReport) -> str:
    return "none available" if report.variants_requested else _NO_VARIANTS


def _hack_reading(entry: ReportEntry) -> str:
    meaning = entry.candidate.meaning
    return f"{entry.label}: {meaning}" if meaning else entry.label


_HANDLE_STATUS = {
    HandleStatus.TAKEN: "taken",
    HandleStatus.NOT_FOUND: "not found (looks free)",
    HandleStatus.INVALID: "not a valid name there",
    HandleStatus.UNKNOWN: "could not check",
}


def _handle_row(handle: HandleResult) -> tuple[str, str, str]:
    """(service, status, where to look or why not)."""
    return handle.service, _HANDLE_STATUS[handle.status], handle.detail or handle.url


def _price(report: BrandReport, entry: ReportEntry) -> str:
    """``"$11.08/yr, renews $12.52"`` for an available name with a known price, else ``""``."""
    if report.prices is None or entry.price is None:
        return ""
    return report.prices.describe(entry.price)


def _with_detail(*parts: str) -> str:
    return " -- ".join(p for p in parts if p)


# ── text ─────────────────────────────────────────────────


def _align(rows: Sequence[Sequence[str]], indent: str = "  ") -> list[str]:
    """Left-align every column but the last; trailing spaces are stripped. ``rows`` is non-empty."""
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    lines = []
    for row in rows:
        cells = [cell.ljust(width) for cell, width in zip(row, widths, strict=False)]
        lines.append((indent + "  ".join([*cells, row[-1]])).rstrip())
    return lines


def _text_section(title: str, rows: Sequence[Sequence[str]], empty: str = _NONE) -> list[str]:
    body = _align(rows) if rows else [f"  {empty}"]
    return ["", title, *body]


def render_text(report: BrandReport) -> str:
    lines = [
        f"Domains for {report.name!r}",
        f"TLDs: {', '.join(t.suffix for t in report.tlds) or _NONE}",
    ]
    lines += _text_section(
        "Exact name",
        [(e.name, e.status.value, _with_detail(e.detail, _price(report, e))) for e in report.exact],
    )
    hack_rows = [
        (e.name, e.status.value, _with_detail(_hack_reading(e), e.detail, _price(report, e)))
        for e in report.hacks
    ]
    hack_lines = _text_section("Domain hacks", hack_rows)
    if not report.suffix_hack_found:
        hack_lines.insert(2, f"  {report.no_suffix_hack_note}")
        if not hack_rows:
            hack_lines.pop()  # the note replaces "(none)"
    lines += hack_lines
    lines += _text_section(
        "Brand variants (available)",
        [(e.name, _with_detail(e.label, e.detail, _price(report, e))) for e in report.variants],
        _variants_empty(report),
    )
    lines += _text_section("Taken", [(e.name, e.status.value, e.detail) for e in report.taken])
    lines += _text_section(
        "Could not verify", [(e.name, e.status.value, e.detail) for e in report.unverifiable]
    )
    lines += _text_section("DNS conflicts", [(e.name, e.detail) for e in report.dns_conflicts])
    if report.handles is not None:
        lines += _text_section(
            f"Handles for {report.name!r}", [_handle_row(h) for h in report.handles]
        )
    rec_rows = [
        (f"{r.rank}.", r.entry.name, _with_detail(r.reason, _price(report, r.entry)))
        for r in report.recommendations
    ]
    lines += _text_section("Recommendation", rec_rows, "nothing available to recommend")
    if report.more_available:
        lines.append(f"  (+{report.more_available} more available above)")
    lines += [f"  - {line}" for line in report.advice]
    notes = _notes(report)
    if notes:
        lines += ["", "Notes", *(f"  - {note}" for note in notes)]
    return "\n".join(lines) + "\n"


def _notes(report: BrandReport) -> list[str]:
    """The report's notes, plus the price caveat when a price is shown."""
    notes = list(report.notes)
    entries = (*report.exact, *report.hacks, *report.variants)
    if report.prices is not None and any(e.price is not None for e in entries):
        notes.append(report.prices.caveat)
    return notes


# ── markdown ─────────────────────────────────────────────


def _cell(text: str) -> str:
    return text.replace("|", "\\|") if text else ""


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    out = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    out += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return out


def _md_section(
    title: str, headers: Sequence[str], rows: Sequence[Sequence[str]], empty: str = "None."
) -> list[str]:
    body = _md_table(headers, rows) if rows else [f"_{empty}_"]
    return ["", f"## {title}", "", *body]


def render_markdown(report: BrandReport) -> str:
    tlds = ", ".join(f"`.{t.suffix}`" for t in report.tlds) or "none"
    lines = [f"# Domains for `{report.name}`", "", f"TLDs: {tlds}"]
    status_headers = ("Domain", "Status", "Details")

    def code(entry: ReportEntry) -> str:
        return f"`{entry.name}`"

    lines += _md_section(
        "Exact name",
        status_headers,
        [
            (code(e), e.status.value, _with_detail(e.detail, _price(report, e)))
            for e in report.exact
        ],
    )
    hack_section = _md_section(
        "Domain hacks",
        ("Domain", "Status", "Reads as", "Details"),
        [
            (code(e), e.status.value, _hack_reading(e), _with_detail(e.detail, _price(report, e)))
            for e in report.hacks
        ],
    )
    if not report.suffix_hack_found:
        note = f"_{report.no_suffix_hack_note[0].upper()}{report.no_suffix_hack_note[1:]}._"
        if report.hacks:
            hack_section[3:3] = [note, ""]
        else:
            hack_section[-1] = note
    lines += hack_section
    lines += _md_section(
        "Brand variants (available)",
        ("Domain", "Reads as", "Details"),
        [(code(e), e.label, _with_detail(e.detail, _price(report, e))) for e in report.variants],
        _variants_empty(report).capitalize() + ".",
    )
    lines += _md_section(
        "Taken", status_headers, [(code(e), e.status.value, e.detail) for e in report.taken]
    )
    lines += _md_section(
        "Could not verify",
        status_headers,
        [(code(e), e.status.value, e.detail) for e in report.unverifiable],
    )
    lines += _md_section(
        "DNS conflicts", ("Domain", "Details"), [(code(e), e.detail) for e in report.dns_conflicts]
    )
    if report.handles is not None:
        lines += _md_section(
            f"Handles for `{report.name}`",
            ("Service", "Status", "Details"),
            [_handle_row(h) for h in report.handles],
        )
    lines += ["", "## Recommendation", ""]
    if report.recommendations:
        lines += [
            f"{r.rank}. **`{r.entry.name}`**: {_with_detail(r.reason, _price(report, r.entry))}"
            for r in report.recommendations
        ]
    else:
        lines.append("_Nothing available to recommend._")
    if report.more_available:
        lines += ["", f"_+{report.more_available} more available above._"]
    if report.advice:
        lines += ["", *(f"- {line}" for line in report.advice)]
    notes = _notes(report)
    if notes:
        lines += ["", "## Notes", "", *(f"- {note}" for note in notes)]
    return "\n".join(lines) + "\n"


# ── json ─────────────────────────────────────────────────


def _entry_json(entry: ReportEntry) -> dict[str, Any]:
    data: dict[str, Any] = {
        "domain": entry.name,
        "fqdn": entry.fqdn,
        "kind": entry.kind.value,
        "label": entry.label,
        "status": entry.status.value,
        "detail": entry.detail,
    }
    if entry.candidate.meaning:
        data["meaning"] = entry.candidate.meaning
    if entry.price is not None:
        data["price"] = {
            "registration": _amount(entry.price.registration),
            "renewal": _amount(entry.price.renewal),
        }
    result = entry.result
    if result is not None and (result.registered_at or result.expires_at or result.statuses):
        data["registered_at"] = result.registered_at.isoformat() if result.registered_at else None
        data["expires_at"] = result.expires_at.isoformat() if result.expires_at else None
        data["statuses"] = list(result.statuses)
    if result is not None and result.registrar:
        data["registrar"] = result.registrar
    if result is not None and result.parked_hint:
        data["parked_hint"] = result.parked_hint
    if result is not None and result.dns is not None:
        data["dns"] = {
            "nameservers": list(result.dns.nameservers),
            "has_address": result.dns.has_address,
            "error": result.dns.error,
            "for_sale": result.dns.for_sale,
        }
    return data


def _amount(value: Decimal | None) -> str | None:
    return f"{value:.2f}" if value is not None else None


def report_to_dict(report: BrandReport) -> dict[str, Any]:
    """The report as plain JSON-serialisable data (what ``--format json`` prints)."""
    return {
        "name": report.name,
        "tlds": [t.suffix for t in report.tlds],
        "exact": [_entry_json(e) for e in report.exact],
        "hacks": [_entry_json(e) for e in report.hacks],
        "suffix_hack_found": report.suffix_hack_found,
        "variants_requested": report.variants_requested,
        "variants": [_entry_json(e) for e in report.variants],
        "taken": [_entry_json(e) for e in report.taken],
        "unverifiable": [_entry_json(e) for e in report.unverifiable],
        "dns_conflicts": [_entry_json(e) for e in report.dns_conflicts],
        "recommendations": [
            {"rank": r.rank, "domain": r.entry.name, "score": r.score, "reason": r.reason}
            for r in report.recommendations
        ],
        "more_available": report.more_available,
        "advice": list(report.advice),
        "notes": list(report.notes),
        "prices": None
        if report.prices is None
        else {
            "source": report.prices.source,
            "retrieved": report.prices.retrieved,
            "currency": report.prices.currency,
            "note": report.prices.caveat,
        },
        "handles": None
        if report.handles is None
        else [
            {
                "service": h.service,
                "handle": h.handle,
                "status": h.status.value,
                "url": h.url,
                "detail": h.detail,
            }
            for h in report.handles
        ],
    }


def render_json(report: BrandReport) -> str:
    return json.dumps(report_to_dict(report), indent=2, ensure_ascii=False) + "\n"


RENDERERS: dict[str, Renderer] = {
    "text": render_text,
    "markdown": render_markdown,
    "json": render_json,
}
