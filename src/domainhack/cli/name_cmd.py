"""``domainhack name NAME``: find a domain for a brand name, in one command.

Checks the exact name under each requested TLD, domain hacks (the name split
at a TLD it ends with, or the name plus a word that ends in one) and, with
``--variants``, brand variants; then prints a report with a recommendation.
Checking reuses ``CheckDomainsUseCase`` and the ``check`` wiring in
``cli.app`` (registrar catalog and router, parallel lanes, cache, circuit
breaker, progress bar).

``find_name`` does the work and returns the report; ``cmd_name`` is the CLI
around it (argument parsing, rendering, exit status). The MCP server calls
``find_name`` directly.
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
from domainhack.adapters.registry_sources import describe_unsupported, unsupported_message
from domainhack.cli import app
from domainhack.domain.entities import DomainCheckResult
from domainhack.ports.dns_lookup import DnsLookup
from domainhack.ports.known_tlds import KnownTlds
from domainhack.ports.result_writer import ResultWriter
from domainhack.usecases.brand_candidates import BrandCandidates, resolve_tld_spec
from domainhack.usecases.brand_report import BrandReport, build_brand_report
from domainhack.usecases.check_domains import CheckDomainsUseCase, CheckSummary
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


def default_services() -> NameServices:
    """Production wiring for ``name``: the known-TLD list and the DNS lookup."""
    return NameServices(known_tlds=IanaTldList(), dns=DnsPythonLookup())


class NameUsageError(app.CliError):
    """The request cannot be run as given (no valid TLD, invalid name)."""


@dataclass(frozen=True)
class NameOutcome:
    """What ``find_name`` returns: the report, the check summary and the skipped count."""

    report: BrandReport
    summary: CheckSummary
    skipped: int


def _confirm_with_dns(
    lookup: DnsLookup, results: Sequence[DomainCheckResult]
) -> list[DomainCheckResult]:
    return ConfirmWithDns(lookup).apply(results)


class ResultCollector(ResultWriter):
    """A ResultWriter that keeps every result in memory."""

    def __init__(self) -> None:
        self.results: list[DomainCheckResult] = []

    def write_result(self, result: DomainCheckResult) -> None:
        self.results.append(result)


def validate_brand_name(value: str) -> str:
    """``value`` as a single lowercase name without a TLD, or raise ValueError."""
    name = value.strip().lower()
    if not name or any(c.isspace() for c in name):
        raise ValueError(f"not a single name: {value!r}")
    if "." in name:
        raise ValueError(
            f"give the name without a TLD (e.g. {name.split('.')[0]!r}), got {value!r}"
        )
    return name


def _brand_name(value: str) -> str:
    try:
        return validate_brand_name(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


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


def find_name(
    name: str,
    tlds: str,
    *,
    run: argparse.Namespace,
    variants: bool = False,
    confirm_dns: bool | None = None,
    catalog: app.RegistrarCatalog | None = None,
    services: NameServices | None = None,
    open_file_writer: Callable[[], ResultWriter] | None = None,
    warn: Callable[[str], None] = app._stderr,
) -> NameOutcome:
    """Check ``name`` under ``tlds`` (presets and suffixes, mixed) and build the report.

    ``run`` holds the shared run options (``app.add_run_options``: delay,
    parallelism, cache, contact, progress). ``confirm_dns`` None means "when
    a DNS lookup is available". ``open_file_writer`` is called once the run
    is known to be possible, so an impossible run creates no output file.
    Warnings go to ``warn`` (stderr by default).

    Raises ``NameUsageError`` when no requested TLD is valid or the name is
    not a valid label, and ``app.NoSupportedTLDError`` when no registrar
    supports any candidate.
    """
    services = services if services is not None else default_services()
    notes: list[str] = []
    selection = resolve_tld_spec(tlds, load_tld_presets(), services.supported_tlds)
    for suffix in selection.rejected:
        notes.append(f".{suffix} skipped: not a TLD this version can check")
        warn(f"warning: .{suffix} is not a TLD this version can check; skipping")
    if not selection.tlds:
        raise NameUsageError(f"no valid TLD in --tlds {tlds!r}")

    confirm = confirm_dns if confirm_dns is not None else services.dns is not None
    if confirm and services.dns is None:
        warn("warning: no DNS resolver available; results are not confirmed in DNS")
        confirm = False

    generator = BrandCandidates(
        name,
        selection.tlds,
        services.known_tlds,
        variants=variants,
        hack_words=load_hack_words(),
    )
    candidates = generator.execute()
    if not candidates:
        raise NameUsageError(f"{name!r} is not a valid domain name label")
    if generator.skipped:
        notes.append(f"skipped {generator.skipped} invalid candidates")
    for suffix in generator.unusable_suffixes:
        notes.append(f"hack under .{suffix} skipped: not a TLD this version can check")

    router = app._build_router(run, catalog)
    try:
        unsupported = {c.domain.tld for c in candidates if not router.supports(c.domain.tld)}
        domains = [c.domain for c in candidates if c.domain.tld not in unsupported]
        requested = [t.suffix for t in selection.tlds if t in unsupported]
        for message in describe_unsupported(requested):
            warn(f"warning: {message}; skipping")
        if not domains:
            raise app.NoSupportedTLDError(f"no registrar supports any candidate TLD for {name!r}")
        file_writer = open_file_writer() if open_file_writer is not None else None
    except BaseException:
        router.close()
        raise

    collector = ResultCollector()
    with app._build_registrar(run, router) as registrar:
        cache = registrar if isinstance(registrar, CachedRegistrarClient) else None
        use_case = CheckDomainsUseCase(
            router,
            collector,
            app._build_progress(run),
            cache=cache,
            parallel=run.parallel,
            lane_key=app._lane_key(run, router),
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
        variants_requested=variants,
        unsupported=unsupported,
        notes=notes,
        unsupported_reasons={t: unsupported_message(t.suffix) for t in unsupported},
    )
    return NameOutcome(report, summary, generator.skipped)


def cmd_name(
    args: argparse.Namespace,
    *,
    catalog: app.RegistrarCatalog | None = None,
    services: NameServices | None = None,
) -> int:
    output: Path | None = args.output
    fmt = app._resolve_output_format(output, None) if output is not None else None
    open_file_writer: Callable[[], ResultWriter] | None = None
    if output is not None and fmt is not None:
        path, chosen = output, fmt

        def open_file_writer() -> ResultWriter:
            return app.build_file_writer(path, chosen)

    try:
        outcome = find_name(
            args.name,
            args.tlds,
            run=args,
            variants=args.variants,
            confirm_dns=args.confirm_dns,
            catalog=catalog,
            services=services,
            open_file_writer=open_file_writer,
        )
    except NameUsageError as exc:
        app._stderr(f"error: {exc}")
        return app.EXIT_USAGE
    sys.stdout.write(RENDERERS[args.format](outcome.report))
    app._warn_skipped(outcome.skipped)
    return app._report(outcome.summary)
