"""The ``name`` report: sections, ranking, advice and the three renderers."""

import json
from datetime import datetime, timezone

import pytest

from domainhack.adapters.brand_report_render import (
    RENDERERS,
    render_json,
    render_markdown,
    render_text,
)
from domainhack.domain.entities import TLD, Availability, DnsEvidence, DomainCheckResult
from domainhack.usecases.brand_candidates import BrandCandidate, CandidateKind
from domainhack.usecases.brand_report import (
    RECOMMENDATION_LIMIT,
    BrandReport,
    EntryStatus,
    build_brand_report,
    score,
)
from tests.fakes import hack

UTC = timezone.utc
TLDS = (TLD("com"), TLD("app"), TLD("io"), TLD("dev"), TLD("xyz"), TLD("so"), TLD("mx"))


def _c(sld: str, tld: str, kind: CandidateKind, label: str = "sumanda", meaning: str = ""):  # type: ignore[no-untyped-def]
    return BrandCandidate(hack(sld, tld), kind, label, meaning)


EXACT = CandidateKind.EXACT
CANDIDATES = [
    _c("sumanda", "com", EXACT),
    _c("sumanda", "app", EXACT),
    _c("sumanda", "io", EXACT),
    _c("sumanda", "dev", EXACT),
    _c("sumanda", "xyz", EXACT),
    _c("sumanda", "so", EXACT),
    _c("sumanda", "mx", EXACT),
    _c("sumandastud", "io", CandidateKind.HACK, "sumanda studio", "a studio"),
    _c("getsumanda", "com", CandidateKind.VARIANT, "get sumanda"),
    _c("sumandahq", "com", CandidateKind.VARIANT, "sumanda hq"),
]


def _r(sld: str, tld: str, availability: Availability, **kw):  # type: ignore[no-untyped-def]
    return DomainCheckResult(hack(sld, tld), availability, **kw)


RESULTS = [
    # Completion order, deliberately not candidate order.
    _r("sumandahq", "com", Availability.TAKEN),
    _r("sumanda", "so", Availability.ERROR, error_message="registry unreachable (rdap.nic.so)"),
    _r(
        "sumanda",
        "com",
        Availability.TAKEN,
        registered_at=datetime(2015, 11, 12, tzinfo=UTC),
        expires_at=datetime(2026, 11, 12, tzinfo=UTC),
        registrar="Example Registrar",
        parked_hint="domainrecover",
        dns=DnsEvidence(nameservers=("ns1.domainrecover.com",)),
    ),
    _r("sumanda", "app", Availability.AVAILABLE, dns=DnsEvidence()),
    _r("sumanda", "io", Availability.AVAILABLE, dns=DnsEvidence(error="timeout")),
    _r("sumanda", "dev", Availability.AVAILABLE, dns=DnsEvidence(nameservers=("ns1.x.net",))),
    _r(
        "sumanda",
        "xyz",
        Availability.TAKEN,
        statuses=("pending delete",),
        expires_at=datetime(2026, 10, 20, tzinfo=UTC),
        dns=DnsEvidence(),
    ),
    _r("sumandastud", "io", Availability.AVAILABLE),
    _r("getsumanda", "com", Availability.AVAILABLE),
]


def _report(**kw) -> BrandReport:  # type: ignore[no-untyped-def]
    defaults = {
        "tlds": TLDS,
        "suffix_hack_found": False,
        "variants_requested": True,
        "unsupported": {TLD("mx")},
        "notes": ["skipped 1 invalid candidates"],
    }
    defaults.update(kw)
    return build_brand_report("sumanda", CANDIDATES, RESULTS, **defaults)


def _names(entries) -> list[str]:  # type: ignore[no-untyped-def]
    return [e.name for e in entries]


class TestSections:
    def test_exact_in_tld_order_with_status(self) -> None:
        report = _report()
        assert [(e.name, e.status) for e in report.exact] == [
            ("sumanda.com", EntryStatus.TAKEN),
            ("sumanda.app", EntryStatus.AVAILABLE),
            ("sumanda.io", EntryStatus.AVAILABLE),
            ("sumanda.dev", EntryStatus.DNS_CONFLICT),
            ("sumanda.xyz", EntryStatus.DROPPING),
            ("sumanda.so", EntryStatus.ERROR),
            ("sumanda.mx", EntryStatus.UNSUPPORTED),
        ]

    def test_details(self) -> None:
        by_name = {e.name: e.detail for e in _report().exact}
        assert by_name["sumanda.com"] == (
            "since 2015-11-12, expires 2026-11-12, registrar Example Registrar, "
            "parked: domainrecover"
        )
        assert by_name["sumanda.app"] == "DNS: not delegated"
        assert by_name["sumanda.io"] == "DNS lookup failed: timeout"
        assert (
            by_name["sumanda.dev"] == "registry says available, but DNS delegates it to ns1.x.net"
        )
        assert by_name["sumanda.xyz"] == (
            "expires 2026-10-20, dropping: pending delete, no DNS delegation"
        )
        assert by_name["sumanda.so"] == "registry unreachable (rdap.nic.so)"
        assert by_name["sumanda.mx"] == "no registrar supports .mx"

    def test_other_sections(self) -> None:
        report = _report()
        assert _names(report.variants) == ["getsumanda.com"]  # available only
        assert _names(report.hacks) == ["sumandastud.io"]
        assert _names(report.taken) == ["sumanda.com", "sumanda.xyz", "sumandahq.com"]
        assert _names(report.unverifiable) == ["sumanda.so", "sumanda.mx"]
        assert _names(report.dns_conflicts) == ["sumanda.dev"]

    def test_missing_result_without_unsupported_tld_is_not_checked(self) -> None:
        report = build_brand_report(
            "sumanda",
            CANDIDATES[:1],
            [],
            tlds=TLDS[:1],
            suffix_hack_found=False,
            variants_requested=False,
        )
        (entry,) = report.unverifiable
        assert entry.status is EntryStatus.NOT_CHECKED
        assert entry.detail == "not checked (run interrupted)"

    def test_does_not_depend_on_result_order(self) -> None:
        assert _report() == build_brand_report(
            "sumanda",
            CANDIDATES,
            list(reversed(RESULTS)),
            tlds=TLDS,
            suffix_hack_found=False,
            variants_requested=True,
            unsupported={TLD("mx")},
            notes=["skipped 1 invalid candidates"],
        )


class TestRecommendation:
    def test_ranking_excludes_taken_conflicts_and_unverifiable(self) -> None:
        recs = _report().recommendations
        assert [(r.rank, r.entry.name, r.reason) for r in recs] == [
            (1, "sumanda.io", "exact name, strong TLD .io"),
            (2, "sumanda.app", "exact name, strong TLD .app"),
            (3, "getsumanda.com", "brand variant on .com (get sumanda)"),
            (4, "sumandastud.io", "domain hack (sumanda studio)"),
        ]

    def test_advice_for_taken_com_and_dropping(self) -> None:
        assert _report().advice == (
            "sumanda.com is taken (expires 2026-11-12); parked at domainrecover: "
            "may be purchasable from the owner",
            "sumanda.xyz is dropping (pending delete): it may be free soon, "
            "but cannot be registered yet",
        )

    def test_taken_com_without_details(self) -> None:
        report = build_brand_report(
            "x",
            [_c("x", "com", EXACT, "x")],
            [_r("x", "com", Availability.TAKEN)],
            tlds=TLDS[:1],
            suffix_hack_found=False,
            variants_requested=False,
        )
        assert report.advice == ("x.com is taken",)
        assert report.recommendations == ()

    @pytest.mark.parametrize(
        ("candidate", "expected"),
        [
            (_c("sumanda", "com", EXACT), 100),
            (_c("sumanda", "ai", EXACT), 90),
            (_c("sumanda", "xyz", EXACT), 70),
            (_c("getsumanda", "com", CandidateKind.VARIANT, "get sumanda"), 60),
            (_c("pla", "to", CandidateKind.HACK, "plato"), 50),
            (_c("getsumanda", "io", CandidateKind.VARIANT, "get sumanda"), 45),
            (_c("sumandastud", "io", CandidateKind.HACK, "sumanda studio"), 40),
        ],
    )
    def test_score_tiers(self, candidate: BrandCandidate, expected: int) -> None:
        from domainhack.usecases.brand_report import ReportEntry

        entry = ReportEntry(candidate, EntryStatus.AVAILABLE)
        assert score(entry) == expected * 100 - len(entry.name)

    def test_tier_beats_length_and_shorter_wins_within_a_tier(self) -> None:
        candidates = [
            _c("sumanda", "xyz", EXACT),
            _c("abc", "xyz", EXACT, "abc"),
            _c("averyveryverylongname", "com", EXACT, "averyveryverylongname"),
        ]
        results = [
            _r(c.domain.sld, c.domain.tld.suffix, Availability.AVAILABLE) for c in candidates
        ]
        report = build_brand_report(
            "n", candidates, results, tlds=TLDS, suffix_hack_found=False, variants_requested=False
        )
        assert [r.entry.name for r in report.recommendations] == [
            "averyveryverylongname.com",
            "abc.xyz",
            "sumanda.xyz",
        ]

    def test_limit(self) -> None:
        candidates = [_c(f"n{i}", "com", EXACT, f"n{i}") for i in range(RECOMMENDATION_LIMIT + 2)]
        results = [_r(c.domain.sld, "com", Availability.AVAILABLE) for c in candidates]
        report = build_brand_report(
            "n", candidates, results, tlds=TLDS, suffix_hack_found=False, variants_requested=False
        )
        assert len(report.recommendations) == RECOMMENDATION_LIMIT
        assert report.more_available == 2


TEXT = (
    "Domains for 'sumanda'\n"
    "TLDs: com, app, io, dev, xyz, so, mx\n"
    "\n"
    "Exact name\n"
    "  sumanda.com  taken         since 2015-11-12, expires 2026-11-12, "
    "registrar Example Registrar, parked: domainrecover\n"
    "  sumanda.app  available     DNS: not delegated\n"
    "  sumanda.io   available     DNS lookup failed: timeout\n"
    "  sumanda.dev  dns-conflict  registry says available, "
    "but DNS delegates it to ns1.x.net\n"
    "  sumanda.xyz  dropping      expires 2026-10-20, "
    "dropping: pending delete, no DNS delegation\n"
    "  sumanda.so   error         registry unreachable (rdap.nic.so)\n"
    "  sumanda.mx   unsupported   no registrar supports .mx\n"
    "\n"
    "Domain hacks\n"
    "  no TLD is a suffix of 'sumanda'\n"
    "  sumandastud.io  available  sumanda studio: a studio\n"
    "\n"
    "Brand variants (available)\n"
    "  getsumanda.com  get sumanda\n"
    "\n"
    "Taken\n"
    "  sumanda.com    taken     since 2015-11-12, expires 2026-11-12, "
    "registrar Example Registrar, parked: domainrecover\n"
    "  sumanda.xyz    dropping  expires 2026-10-20, "
    "dropping: pending delete, no DNS delegation\n"
    "  sumandahq.com  taken\n"
    "\n"
    "Could not verify\n"
    "  sumanda.so  error        registry unreachable (rdap.nic.so)\n"
    "  sumanda.mx  unsupported  no registrar supports .mx\n"
    "\n"
    "DNS conflicts\n"
    "  sumanda.dev  registry says available, but DNS delegates it to ns1.x.net\n"
    "\n"
    "Recommendation\n"
    "  1.  sumanda.io      exact name, strong TLD .io\n"
    "  2.  sumanda.app     exact name, strong TLD .app\n"
    "  3.  getsumanda.com  brand variant on .com (get sumanda)\n"
    "  4.  sumandastud.io  domain hack (sumanda studio)\n"
    "  - sumanda.com is taken (expires 2026-11-12); parked at domainrecover: "
    "may be purchasable from the owner\n"
    "  - sumanda.xyz is dropping (pending delete): it may be free soon, "
    "but cannot be registered yet\n"
    "\n"
    "Notes\n"
    "  - skipped 1 invalid candidates\n"
)


class TestRenderers:
    def test_text(self) -> None:
        assert render_text(_report()) == TEXT

    def test_text_empty_sections(self) -> None:
        report = build_brand_report(
            "plato",
            [_c("pla", "to", CandidateKind.HACK, "plato")],
            [_r("pla", "to", Availability.TAKEN)],
            tlds=(),
            suffix_hack_found=True,
            variants_requested=False,
        )
        text = render_text(report)
        assert "TLDs: (none)" in text
        assert "no TLD is a suffix" not in text
        assert "Brand variants (available)\n  not requested (add --variants)\n" in text
        assert "Could not verify\n  (none)\n" in text
        assert "Recommendation\n  nothing available to recommend\n" in text
        assert "Notes" not in text

    def test_text_no_hacks_at_all(self) -> None:
        report = build_brand_report(
            "zz", [], [], tlds=(), suffix_hack_found=False, variants_requested=True
        )
        text = render_text(report)
        assert "Domain hacks\n  no TLD is a suffix of 'zz'\n\nBrand variants" in text
        assert "Brand variants (available)\n  none available\n" in text

    def test_markdown(self) -> None:
        md = render_markdown(_report())
        assert md.startswith(
            "# Domains for `sumanda`\n\n"
            "TLDs: `.com`, `.app`, `.io`, `.dev`, `.xyz`, `.so`, `.mx`\n\n"
            "## Exact name\n\n"
            "| Domain | Status | Details |\n"
            "|---|---|---|\n"
            "| `sumanda.com` | taken | since 2015-11-12, expires 2026-11-12, "
            "registrar Example Registrar, parked: domainrecover |\n"
        )
        assert (
            "## Domain hacks\n\n_No TLD is a suffix of 'sumanda'._\n\n"
            "| Domain | Status | Reads as | Details |\n|---|---|---|---|\n"
            "| `sumandastud.io` | available | sumanda studio: a studio |  |\n"
        ) in md
        assert (
            "## Recommendation\n\n"
            "1. **`sumanda.io`**: exact name, strong TLD .io\n"
            "2. **`sumanda.app`**: exact name, strong TLD .app\n"
            "3. **`getsumanda.com`**: brand variant on .com (get sumanda)\n"
            "4. **`sumandastud.io`**: domain hack (sumanda studio)\n\n"
            "- sumanda.com is taken (expires 2026-11-12); parked at domainrecover: "
            "may be purchasable from the owner\n"
        ) in md
        assert md.endswith("## Notes\n\n- skipped 1 invalid candidates\n")

    def test_markdown_empty_sections_and_pipes(self) -> None:
        candidates = [_c("x", "com", EXACT, "x")]
        results = [_r("x", "com", Availability.ERROR, error_message="bad | reply")]
        report = build_brand_report(
            "x", candidates, results, tlds=(), suffix_hack_found=False, variants_requested=False
        )
        md = render_markdown(report)
        assert "TLDs: none" in md
        assert "| `x.com` | error | bad \\| reply |" in md
        assert "## Domain hacks\n\n_No TLD is a suffix of 'x'._\n" in md
        assert "_Not requested (add --variants)._" in md
        assert "## DNS conflicts\n\n_None._" in md
        assert "_Nothing available to recommend._" in md

    def test_markdown_more_available(self) -> None:
        candidates = [_c(f"n{i}", "com", EXACT, f"n{i}") for i in range(RECOMMENDATION_LIMIT + 1)]
        results = [_r(c.domain.sld, "com", Availability.AVAILABLE) for c in candidates]
        report = build_brand_report(
            "n", candidates, results, tlds=(), suffix_hack_found=True, variants_requested=True
        )
        assert "_+1 more available above._" in render_markdown(report)
        assert "(+1 more available above)" in render_text(report)

    def test_json(self) -> None:
        data = json.loads(render_json(_report()))
        assert data["name"] == "sumanda"
        assert data["tlds"] == ["com", "app", "io", "dev", "xyz", "so", "mx"]
        assert data["suffix_hack_found"] is False
        assert data["exact"][0] == {
            "domain": "sumanda.com",
            "fqdn": "sumanda.com",
            "kind": "exact",
            "label": "sumanda",
            "status": "taken",
            "detail": "since 2015-11-12, expires 2026-11-12, registrar Example Registrar, "
            "parked: domainrecover",
            "registered_at": "2015-11-12T00:00:00+00:00",
            "expires_at": "2026-11-12T00:00:00+00:00",
            "statuses": [],
            "registrar": "Example Registrar",
            "parked_hint": "domainrecover",
            "dns": {"nameservers": ["ns1.domainrecover.com"], "has_address": False, "error": ""},
        }
        assert data["hacks"][0]["meaning"] == "a studio"
        assert data["recommendations"][0] == {
            "rank": 1,
            "domain": "sumanda.io",
            "score": 9000 - len("sumanda.io"),
            "reason": "exact name, strong TLD .io",
        }
        assert [e["domain"] for e in data["unverifiable"]] == ["sumanda.so", "sumanda.mx"]
        assert [e["domain"] for e in data["dns_conflicts"]] == ["sumanda.dev"]
        assert data["more_available"] == 0
        assert len(data["advice"]) == 2
        assert data["notes"] == ["skipped 1 invalid candidates"]

    @pytest.mark.parametrize("fmt", sorted(RENDERERS))
    def test_deterministic(self, fmt: str) -> None:
        assert RENDERERS[fmt](_report()) == RENDERERS[fmt](_report())
