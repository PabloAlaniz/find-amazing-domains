"""``domainhack name NAME``: find a domain for a brand name, in one command.

Checks the exact name under each requested TLD, domain hacks (the name split
at a TLD it ends with, or the name plus a word that ends in one) and, with
``--variants``, brand variants; then prints a report with a recommendation.
Checking reuses ``CheckDomainsUseCase`` and the ``check`` wiring in
``cli.app`` (registrar catalog and router, parallel lanes, cache, circuit
breaker, progress bar).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from domainhack.adapters.brand_data import DEFAULT_PRESET, load_hack_words, load_tld_presets
from domainhack.adapters.brand_report_render import RENDERERS
from domainhack.adapters.cached_registrar import CachedRegistrarClient
from domainhack.adapters.dns_resolver import DnsPythonLookup
from domainhack.adapters.iana_tlds import IanaTldList
from domainhack.adapters.registrar_catalog import supported_tlds
from domainhack.cli import app
from domainhack.domain.entities import DomainCheckResult
from domainhack.ports.dns_lookup import DnsLookup
from domainhack.ports.known_tlds import KnownTlds
from domainhack.ports.result_writer import ResultWriter
from domainhack.usecases.brand_candidates import BrandCandidates, resolve_tld_spec
from domainhack.usecases.brand_report import build_brand_report
from domainhack.usecases.check_domains import CheckDomainsUseCase
from domainhack.usecases.confirm_dns import ConfirmWithDns

REPORT_FORMATS = tuple(RENDERERS)


@dataclass(frozen=True)
class NameServices:
    """What ``name`` needs besides the registrar catalog (tests pass fakes).

    ``dns`` is optional: without it, results are not confirmed against DNS.
    ``supported_tlds`` lists the catalog's TLDs for the ``all-supported`` preset.
    """

    known_tlds: KnownTlds
    dns: DnsLookup | None = None
    supported_tlds: Callable[[], Iterable[str]] = supported_tlds


def _default_services() -> NameServices:
    """Production wiring for ``name``: the known-TLD list and the DNS lookup."""
    return NameServices(known_tlds=IanaTldList(), dns=DnsPythonLookup())


def _confirm_with_dns(
    lookup: DnsLookup, results: Sequence[DomainCheckResult]
) -> list[DomainCheckResult]:
    return ConfirmWithDns(lookup).apply(results)


class _Collector(ResultWriter):
    def __init__(self) -> None:
        self.results: list[DomainCheckResult] = []

    def write_result(self, result: DomainCheckResult) -> None:
        self.results.append(result)


def _brand_name(value: str) -> str:
    name = value.strip().lower()
    if not name or any(c.isspace() for c in name):
        raise argparse.ArgumentTypeError(f"not a single name: {value!r}")
    if "." in name:
        raise argparse.ArgumentTypeError(
            f"give the name without a TLD (e.g. {name.split('.')[0]!r}), got {value!r}"
        )
    return name


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser], *, epilog: str) -> None:
    """Add the ``name`` subcommand to the CLI's subparsers."""
    presets = ", ".join(load_tld_presets())
    parser = sub.add_parser(
        "name",
        help="Find a domain for a brand name (exact, hacks, variants) and recommend one",
        description="Check NAME under a set of TLDs, look for domain hacks that spell it "
        "(sumanda -> sumandastud.io) and, with --variants, brand variants "
        "(getsumanda.com); then print a report with a recommendation.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("name", type=_brand_name, help="The name, without a TLD (e.g. sumanda)")
    parser.add_argument(
        "--tlds",
        default=DEFAULT_PRESET,
        metavar="PRESET|TLD[,...]",
        help=f"Presets ({presets}) and TLDs, mixed: startup,com.ar,la (default: {DEFAULT_PRESET})",
    )
    parser.add_argument(
        "--variants",
        action="store_true",
        help="Also check get-/use-/try-/hola-/my- NAME and NAME -hq/-app/-labs/-studio, "
        "under .com and the first 3 other TLDs",
    )
    parser.add_argument(
        "--format",
        choices=REPORT_FORMATS,
        default="text",
        help="Report format on stdout (default: text)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Also save the raw results to FILE (.csv, .json or .jsonl)",
    )
    parser.add_argument(
        "--confirm-dns",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Confirm results against public DNS (default: on when a DNS resolver is available)",
    )
    app.add_run_options(parser)


def cmd_name(
    args: argparse.Namespace,
    *,
    catalog: app.RegistrarCatalog | None = None,
    services: NameServices | None = None,
) -> int:
    services = services if services is not None else _default_services()
    notes: list[str] = []
    selection = resolve_tld_spec(args.tlds, load_tld_presets(), services.supported_tlds)
    for suffix in selection.rejected:
        notes.append(f".{suffix} skipped: not a TLD this version can check")
        app._stderr(f"warning: .{suffix} is not a TLD this version can check; skipping")
    if not selection.tlds:
        app._stderr(f"error: no valid TLD in --tlds {args.tlds!r}")
        return app.EXIT_USAGE
    output: Path | None = args.output
    fmt = app._resolve_output_format(output, None) if output is not None else None

    confirm = args.confirm_dns if args.confirm_dns is not None else services.dns is not None
    if confirm and services.dns is None:
        app._stderr("warning: no DNS resolver available; results are not confirmed in DNS")
        confirm = False

    generator = BrandCandidates(
        args.name,
        selection.tlds,
        services.known_tlds,
        variants=args.variants,
        hack_words=load_hack_words(),
    )
    candidates = generator.execute()
    if not candidates:
        app._stderr(f"error: {args.name!r} is not a valid domain name label")
        return app.EXIT_USAGE
    if generator.skipped:
        notes.append(f"skipped {generator.skipped} invalid candidates")
    for suffix in generator.unusable_suffixes:
        notes.append(f"hack under .{suffix} skipped: not a TLD this version can check")

    router = app._build_router(args, catalog)
    try:
        unsupported = {c.domain.tld for c in candidates if not router.supports(c.domain.tld)}
        domains = [c.domain for c in candidates if c.domain.tld not in unsupported]
        requested_unsupported = [f".{t.suffix}" for t in selection.tlds if t in unsupported]
        if requested_unsupported:
            app._stderr(
                f"warning: no registrar supports {', '.join(requested_unsupported)}; skipping"
            )
        if not domains:
            raise app.NoSupportedTLDError(
                f"no registrar supports any candidate TLD for {args.name!r}"
            )
        file_writer = app.build_file_writer(output, fmt) if output and fmt else None
    except BaseException:
        router.close()
        raise

    collector = _Collector()
    with app._build_registrar(args, router) as registrar:
        cache = registrar if isinstance(registrar, CachedRegistrarClient) else None
        use_case = CheckDomainsUseCase(
            router,
            collector,
            app._build_progress(args),
            cache=cache,
            parallel=args.parallel,
            lane_key=app._lane_key(args, router),
        )
        summary = use_case.execute(domains, total=len(domains))

    order = {c.domain.fqdn: i for i, c in enumerate(candidates)}
    results = sorted(collector.results, key=lambda r: order[r.domain.fqdn])
    try:
        if confirm and services.dns is not None and not summary.interrupted:
            results = _confirm_with_dns(services.dns, results)
    finally:
        if file_writer is not None:
            for result in results:
                file_writer.write_result(result)
            file_writer.flush()

    if summary.interrupted:
        notes.append("run interrupted: some names were not checked")
    report = build_brand_report(
        generator.name,
        candidates,
        results,
        tlds=selection.tlds,
        suffix_hack_found=generator.suffix_hack_found,
        variants_requested=args.variants,
        unsupported=unsupported,
        notes=notes,
    )
    sys.stdout.write(RENDERERS[args.format](report))
    app._warn_skipped(generator.skipped)
    return app._report(summary)
