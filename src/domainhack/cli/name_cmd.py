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
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from domainhack.adapters.brand_data import DEFAULT_PRESET, load_hack_words, load_tld_presets
from domainhack.adapters.brand_report_render import RENDERERS
from domainhack.adapters.cached_registrar import CachedRegistrarClient
from domainhack.adapters.config_file import Config
from domainhack.adapters.dns_resolver import DnsPythonLookup
from domainhack.adapters.handle_http import HttpHandleChecker
from domainhack.adapters.iana_tlds import IanaTldList
from domainhack.adapters.registrar_catalog import supported_tlds
from domainhack.adapters.registry_sources import describe_unsupported, unsupported_message
from domainhack.adapters.tld_prices import load_price_list
from domainhack.cli import app
from domainhack.domain.entities import DomainCheckResult
from domainhack.domain.pricing import PriceList
from domainhack.ports.dns_lookup import DnsLookup
from domainhack.ports.handle_checker import HandleChecker, HandleResult
from domainhack.ports.known_tlds import KnownTlds
from domainhack.ports.result_writer import ResultWriter
from domainhack.usecases.brand_candidates import (
    MAX_AFFIXES,
    BrandCandidates,
    Presets,
    resolve_tld_spec,
)
from domainhack.usecases.brand_report import BrandReport, build_brand_report
from domainhack.usecases.check_domains import CheckDomainsUseCase, CheckSummary
from domainhack.usecases.confirm_dns import ConfirmWithDns

REPORT_FORMATS = tuple(RENDERERS)
_AFFIX_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


@dataclass(frozen=True)
class NameServices:
    """What ``name`` needs besides the registrar catalog (tests pass fakes).

    ``dns`` is optional: without it, results are not confirmed against DNS.
    ``supported_tlds`` lists the catalog's TLDs for the ``all-supported`` preset.
    ``handles`` checks the name on GitHub, npm and PyPI when asked to; without
    it, the production checker is built with the run's contact address.
    ``prices`` loads the reference price list (None: this build has none).
    """

    known_tlds: KnownTlds
    dns: DnsLookup | None = None
    supported_tlds: Callable[[], Iterable[str]] = supported_tlds
    handles: HandleChecker | None = None
    prices: Callable[[], PriceList | None] = load_price_list


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


def parse_affixes(value: str) -> list[str]:
    """``"get,try"`` as a list of lowercase words to put before or after a name.

    Raises ValueError when the list is empty, too long or has a word that is
    not letters, digits and inner hyphens.
    """
    words = list(dict.fromkeys(w.strip().lower() for w in value.split(",") if w.strip()))
    if not words:
        raise ValueError("at least one word is required")
    if len(words) > MAX_AFFIXES:
        raise ValueError(f"at most {MAX_AFFIXES} words are allowed, got {len(words)}")
    for word in words:
        if not _AFFIX_RE.fullmatch(word):
            raise ValueError(f"not letters, digits and inner hyphens: {word!r}")
    return words


def _affixes(value: str) -> list[str]:
    try:
        return parse_affixes(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def register(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    epilog: str,
    config: Config | None = None,
) -> None:
    """Add the ``name`` subcommand to the CLI's subparsers.

    ``config`` (a loaded configuration file) adds its presets and supplies
    the defaults of ``--tlds`` and the run options.
    """
    config = config if config is not None else Config()
    presets = ", ".join(load_tld_presets(config.presets))
    default_tlds = config.name_tlds or DEFAULT_PRESET
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
        default=default_tlds,
        metavar="PRESET|TLD[,...]",
        help=f"Presets ({presets}) and TLDs, mixed: startup,com.ar,la (default: {default_tlds})",
    )
    parser.add_argument(
        "--variants",
        action="store_true",
        help="Also check get-/use-/try-/hola-/my- NAME and NAME -hq/-app/-labs/-studio, "
        "under .com and the first 3 other TLDs",
    )
    parser.add_argument(
        "--prefix",
        type=_affixes,
        default=None,
        metavar="WORD[,...]",
        help="Your own words to put before NAME instead of get/use/try/hola/my "
        f"(at most {MAX_AFFIXES}): --prefix get,try -> getNAME, tryNAME. Turns variants on",
    )
    parser.add_argument(
        "--suffix",
        type=_affixes,
        default=None,
        metavar="WORD[,...]",
        help="Your own words to put after NAME instead of hq/app/labs/studio "
        f"(at most {MAX_AFFIXES}): --suffix hq,labs -> NAMEhq, NAMElabs. Turns variants on",
    )
    parser.add_argument(
        "--subdomain-hacks",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When NAME ends in a TLD, also check hacks that put part of it in a subdomain "
        "(delicious -> del.icio.us, registering icio.us). Default: on",
    )
    parser.add_argument(
        "--prices",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show each TLD's reference price next to available names (a dated snapshot of "
        "a registrar's public price list, not a quote). Default: on",
    )
    parser.add_argument(
        "--handles",
        action="store_true",
        help="Also say whether NAME is taken on GitHub (user or organization), npm and "
        "PyPI: one request to each. Information only; it does not change the recommendation",
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
    app.add_config_options(parser)
    app.apply_run_config(parser, config)


def find_name(
    name: str,
    tlds: str,
    *,
    run: argparse.Namespace,
    variants: bool = False,
    prefixes: Sequence[str] | None = None,
    suffixes: Sequence[str] | None = None,
    subdomain_hacks: bool = True,
    presets: Presets | None = None,
    handles: bool = False,
    prices: bool = True,
    confirm_dns: bool | None = None,
    catalog: app.RegistrarCatalog | None = None,
    services: NameServices | None = None,
    open_file_writer: Callable[[], ResultWriter] | None = None,
    warn: Callable[[str], None] = app._stderr,
) -> NameOutcome:
    """Check ``name`` under ``tlds`` (presets and suffixes, mixed) and build the report.

    ``variants`` tries the default prefixes and suffixes; ``prefixes`` and
    ``suffixes`` replace them (see ``brand_candidates.variant_affixes``).
    ``subdomain_hacks`` adds hacks such as ``del.icio.us`` when the name ends
    in a TLD. ``presets`` are extra TLD presets (the user's own). ``handles``
    also asks GitHub, npm and PyPI whether the name is taken there. ``prices``
    shows each TLD's reference price next to available names.

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
    selection = resolve_tld_spec(tlds, load_tld_presets(presets), services.supported_tlds)
    for suffix in selection.rejected:
        notes.append(f".{suffix} skipped: not a TLD this version can check")
        warn(f"warning: .{suffix} is not a TLD this version can check; skipping")
    if not selection.tlds:
        raise NameUsageError(f"no valid TLD in --tlds {tlds!r}")

    confirm = confirm_dns if confirm_dns is not None else services.dns is not None
    if confirm and services.dns is None:
        warn("warning: no DNS resolver available; results are not confirmed in DNS")
        confirm = False

    try:
        generator = BrandCandidates(
            name,
            selection.tlds,
            services.known_tlds,
            variants=variants,
            hack_words=load_hack_words(),
            prefixes=prefixes,
            suffixes=suffixes,
            subdomain_hacks=subdomain_hacks,
        )
    except ValueError as exc:
        raise NameUsageError(f"prefixes and suffixes: {exc}") from None
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
    handle_results: list[HandleResult] | None = None
    if handles and not summary.interrupted:
        checker = services.handles
        if checker is None:
            checker = HttpHandleChecker(contact=getattr(run, "contact", None))
        handle_results = checker.check(generator.name)
    report = build_brand_report(
        generator.name,
        candidates,
        results,
        tlds=selection.tlds,
        suffix_hack_found=generator.suffix_hack_found,
        variants_requested=generator.variants_requested,
        unsupported=unsupported,
        notes=notes,
        unsupported_reasons={t: unsupported_message(t.suffix) for t in unsupported},
        handles=handle_results,
        prices=services.prices() if prices else None,
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

    config: Config = getattr(args, "config_data", None) or Config()
    try:
        outcome = find_name(
            args.name,
            args.tlds,
            run=args,
            variants=args.variants,
            prefixes=args.prefix if args.prefix is not None else config.name_prefixes,
            suffixes=args.suffix if args.suffix is not None else config.name_suffixes,
            subdomain_hacks=args.subdomain_hacks,
            presets=config.presets,
            handles=args.handles,
            prices=args.prices,
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
