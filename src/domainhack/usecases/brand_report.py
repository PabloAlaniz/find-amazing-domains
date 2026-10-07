"""Turn the check results for a brand name into a structured report with a recommendation.

Sections (every list is in candidate order, so the report does not depend on
the order checks completed in):

- ``exact``: ``name.<tld>`` for each requested TLD, whatever its status;
- ``variants``: available brand variants (``getsumanda.com``...);
- ``hacks``: every hack candidate, whatever its status;
- ``taken``: every taken name, with its registration details;
- ``unverifiable``: names that could not be checked (registry error or
  unreachable, no registrar for the TLD, run interrupted), each with the reason;
- ``dns_conflicts``: registry says available, DNS says delegated.

Recommendation score (higher is better)::

    score = tier * 100 - len(name)

    tier  100  exact name under .com
           90  exact name under a strong TLD (.app .io .ai .co)
           70  exact name under any other TLD
           60  brand variant under .com (getsumanda.com)
           50  hack that spells the name itself (plato -> pla.to)
           45  brand variant under another TLD
           40  hack with an extra word (sumandastud.io)

The tier always decides; length (of the name as displayed) only breaks ties
within a tier, then alphabetical order. Only AVAILABLE names without a DNS
conflict are recommended: taken, dropping, DNS-conflict and unverifiable names
never are. Advice lines mention a taken exact ``.com`` (with its parking
service, if any) and dropping names.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from domainhack.domain.entities import TLD, Availability, DomainCheckResult
from domainhack.usecases.brand_candidates import BrandCandidate, CandidateKind

STRONG_TLDS: frozenset[str] = frozenset({"app", "io", "ai", "co"})
RECOMMENDATION_LIMIT = 5

TIER_EXACT_COM = 100
TIER_EXACT_STRONG = 90
TIER_EXACT_OTHER = 70
TIER_VARIANT_COM = 60
TIER_HACK_NAME = 50
TIER_VARIANT_OTHER = 45
TIER_HACK_WORD = 40


class EntryStatus(Enum):
    AVAILABLE = "available"
    TAKEN = "taken"
    DROPPING = "dropping"
    DNS_CONFLICT = "dns-conflict"
    ERROR = "error"
    UNSUPPORTED = "unsupported"
    NOT_CHECKED = "not-checked"


_UNVERIFIABLE = frozenset({EntryStatus.ERROR, EntryStatus.UNSUPPORTED, EntryStatus.NOT_CHECKED})


@dataclass(frozen=True)
class ReportEntry:
    """One candidate with its outcome and a one-line human ``detail``."""

    candidate: BrandCandidate
    status: EntryStatus
    detail: str = ""
    result: DomainCheckResult | None = None

    @property
    def name(self) -> str:
        return self.candidate.domain.display

    @property
    def fqdn(self) -> str:
        return self.candidate.domain.fqdn

    @property
    def kind(self) -> CandidateKind:
        return self.candidate.kind

    @property
    def label(self) -> str:
        return self.candidate.label


@dataclass(frozen=True)
class Recommendation:
    rank: int
    entry: ReportEntry
    score: int
    reason: str


@dataclass(frozen=True)
class BrandReport:
    name: str
    tlds: tuple[TLD, ...]
    exact: tuple[ReportEntry, ...]
    variants: tuple[ReportEntry, ...]
    variants_requested: bool
    hacks: tuple[ReportEntry, ...]
    suffix_hack_found: bool
    taken: tuple[ReportEntry, ...]
    unverifiable: tuple[ReportEntry, ...]
    dns_conflicts: tuple[ReportEntry, ...]
    recommendations: tuple[Recommendation, ...]
    more_available: int  # available names beyond RECOMMENDATION_LIMIT
    advice: tuple[str, ...]
    notes: tuple[str, ...]

    @property
    def no_suffix_hack_note(self) -> str:
        return f"no TLD is a suffix of {self.name!r}"


def _date(result: DomainCheckResult, attr: str) -> str:
    value = getattr(result, attr)
    return value.date().isoformat() if value is not None else ""


def _taken_detail(result: DomainCheckResult) -> str:
    parts: list[str] = []
    if since := _date(result, "registered_at"):
        parts.append(f"since {since}")
    if expires := _date(result, "expires_at"):
        parts.append(f"expires {expires}")
    if result.registrar:
        parts.append(f"registrar {result.registrar}")
    if result.parked_hint:
        parts.append(f"parked: {result.parked_hint}")
    if result.is_dropping:
        parts.append(f"dropping: {', '.join(result.dropping_statuses)}")
    if result.dns is not None and not result.dns.error and not result.dns.is_delegated:
        parts.append("no DNS delegation")
    return ", ".join(parts)


def _classify(candidate: BrandCandidate, result: DomainCheckResult | None, why: str) -> ReportEntry:
    if result is None:
        status = EntryStatus.UNSUPPORTED if why else EntryStatus.NOT_CHECKED
        return ReportEntry(candidate, status, why or "not checked (run interrupted)")
    if result.availability is Availability.ERROR:
        return ReportEntry(
            candidate, EntryStatus.ERROR, result.error_message or "check failed", result
        )
    if result.availability is Availability.TAKEN:
        status = EntryStatus.DROPPING if result.is_dropping else EntryStatus.TAKEN
        return ReportEntry(candidate, status, _taken_detail(result), result)
    dns = result.dns
    if result.dns_conflict:
        assert dns is not None
        ns = ", ".join(dns.nameservers)
        detail = f"registry says available, but DNS delegates it to {ns}"
        return ReportEntry(candidate, EntryStatus.DNS_CONFLICT, detail, result)
    if dns is None:
        detail = ""
    elif dns.error:
        detail = f"DNS lookup failed: {dns.error}"
    else:
        detail = "DNS: not delegated"
    return ReportEntry(candidate, EntryStatus.AVAILABLE, detail, result)


def _tier(entry: ReportEntry) -> tuple[int, str]:
    tld = entry.candidate.domain.tld.suffix
    match entry.kind:
        case CandidateKind.EXACT:
            if tld == "com":
                return TIER_EXACT_COM, "exact name, .com"
            if tld in STRONG_TLDS:
                return TIER_EXACT_STRONG, f"exact name, strong TLD .{tld}"
            return TIER_EXACT_OTHER, f"exact name, .{tld}"
        case CandidateKind.VARIANT:
            if tld == "com":
                return TIER_VARIANT_COM, f"brand variant on .com ({entry.label})"
            return TIER_VARIANT_OTHER, f"brand variant ({entry.label})"
        case CandidateKind.HACK:
            domain = entry.candidate.domain
            if domain.sld + tld.replace(".", "") == entry.label:
                return TIER_HACK_NAME, "domain hack spelling the name"
            return TIER_HACK_WORD, f"domain hack ({entry.label})"
    raise AssertionError(entry.kind)  # pragma: no cover


def score(entry: ReportEntry) -> int:
    """``tier * 100 - len(name)``; see the module docstring."""
    return _tier(entry)[0] * 100 - len(entry.name)


def _advice(entries: Sequence[ReportEntry]) -> list[str]:
    lines: list[str] = []
    for entry in entries:
        result = entry.result
        if result is None or result.availability is not Availability.TAKEN:
            continue
        exact_com = entry.kind is CandidateKind.EXACT and entry.candidate.domain.tld.suffix == "com"
        if entry.status is EntryStatus.DROPPING:
            lines.append(
                f"{entry.name} is dropping ({', '.join(result.dropping_statuses)}): it may be "
                "free soon, but cannot be registered yet"
            )
        elif exact_com:
            line = f"{entry.name} is taken"
            if expires := _date(result, "expires_at"):
                line += f" (expires {expires})"
            if result.parked_hint:
                line += f"; parked at {result.parked_hint}: may be purchasable from the owner"
            lines.append(line)
    return lines


def build_brand_report(
    name: str,
    candidates: Sequence[BrandCandidate],
    results: Iterable[DomainCheckResult],
    *,
    tlds: Sequence[TLD],
    suffix_hack_found: bool,
    variants_requested: bool,
    unsupported: Collection[TLD] = (),
    notes: Sequence[str] = (),
    unsupported_reasons: Mapping[TLD, str] | None = None,
) -> BrandReport:
    """Build the report. Candidates without a result are unverifiable: "no registrar
    supports .x" when their TLD is in ``unsupported`` (or the message given for it
    in ``unsupported_reasons``, which explains why), otherwise "not checked"."""
    reasons = unsupported_reasons or {}
    by_fqdn = {r.domain.fqdn: r for r in results}
    entries = [
        _classify(
            c,
            by_fqdn.get(c.domain.fqdn),
            (reasons.get(c.domain.tld) or f"no registrar supports .{c.domain.tld.suffix}")
            if c.domain.tld in unsupported
            else "",
        )
        for c in candidates
    ]

    def of(kind: CandidateKind) -> list[ReportEntry]:
        return [e for e in entries if e.kind is kind]

    available = [e for e in entries if e.status is EntryStatus.AVAILABLE]
    ranked = sorted(available, key=lambda e: (-score(e), e.name))
    recommendations = tuple(
        Recommendation(i, e, score(e), _tier(e)[1])
        for i, e in enumerate(ranked[:RECOMMENDATION_LIMIT], start=1)
    )
    return BrandReport(
        name=name,
        tlds=tuple(tlds),
        exact=tuple(of(CandidateKind.EXACT)),
        variants=tuple(e for e in of(CandidateKind.VARIANT) if e.status is EntryStatus.AVAILABLE),
        variants_requested=variants_requested,
        hacks=tuple(of(CandidateKind.HACK)),
        suffix_hack_found=suffix_hack_found,
        taken=tuple(e for e in entries if e.status in (EntryStatus.TAKEN, EntryStatus.DROPPING)),
        unverifiable=tuple(e for e in entries if e.status in _UNVERIFIABLE),
        dns_conflicts=tuple(e for e in entries if e.status is EntryStatus.DNS_CONFLICT),
        recommendations=recommendations,
        more_available=max(0, len(ranked) - RECOMMENDATION_LIMIT),
        advice=tuple(_advice(entries)),
        notes=tuple(notes),
    )
