import argparse
import codecs
import os
import re
import sys
from collections.abc import Hashable, Iterable, Sequence
from pathlib import Path
from typing import Protocol

from domainhack import __version__
from domainhack.adapters._circuit import HostCircuitBreaker
from domainhack.adapters.cached_registrar import (
    AVAILABLE_TTL_SECONDS,
    CachedRegistrarClient,
    CacheTtlPolicy,
)
from domainhack.adapters.composite_writer import CompositeResultWriter
from domainhack.adapters.console_writer import ConsoleResultWriter
from domainhack.adapters.csv_writer import CsvResultWriter
from domainhack.adapters.file_word_source import DEFAULT_ENCODING, FileWordSource, WordListError
from domainhack.adapters.json_writer import JsonResultWriter
from domainhack.adapters.pacing import lane_for, pacing_for
from domainhack.adapters.registrar_catalog import build_registrar_for
from domainhack.adapters.registrar_router import RegistrarFactory, RegistrarRouter
from domainhack.adapters.tqdm_progress import TqdmProgressReporter
from domainhack.domain.entities import TLD, DomainHack
from domainhack.ports.progress import NullProgressReporter, ProgressReporter
from domainhack.ports.registrar import RegistrarClient
from domainhack.ports.result_writer import ResultWriter
from domainhack.usecases.check_domains import (
    DEFAULT_PARALLEL,
    CheckDomainsUseCase,
    CheckSummary,
    LaneKey,
)
from domainhack.usecases.estimate_run import (
    GUARDRAIL_MAX_QUERIES,
    Pacing,
    RunEstimate,
    estimate_run,
    format_duration,
)
from domainhack.usecases.filter_words import FilterWordsUseCase
from domainhack.usecases.generate_range import (
    MAX_RANGE_LENGTH,
    RangeCandidatesUseCase,
    RangeWordSource,
)
from domainhack.usecases.rank_candidates import CandidateOrder, RankCandidatesUseCase

OUTPUT_FORMATS = ("csv", "json")
_FORMAT_BY_SUFFIX = {".csv": "csv", ".json": "json", ".jsonl": "json"}

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130  # 128 + SIGINT, the shell convention

CONTACT_ENV = "DOMAINHACK_CONTACT"

_EPILOG = f"""\
exit status:
  {EXIT_OK}    completed and every check succeeded
  {EXIT_FAILURE}    completed but some checks ended in ERROR, or the run failed
       (unreadable word list, unwritable output, no supported TLD...)
  {EXIT_USAGE}    usage error (bad option or value)
  {EXIT_INTERRUPTED}  interrupted (Ctrl-C); results checked so far are kept

Results go to stdout; errors, warnings, progress and the summary go to stderr.
"""

_RANGE_END_RE = re.compile(r"[a-z]+")
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class RegistrarCatalog(Protocol):
    """Builds the registrar client for one TLD, or None when unsupported.

    The production catalog is ``build_registrar_for``; ``main`` and
    ``cmd_check`` accept another one (tests pass a fake).
    """

    def __call__(
        self,
        tld: TLD,
        *,
        delay: float,
        breaker: HostCircuitBreaker | None,
        contact: str | None,
    ) -> RegistrarClient | None: ...


class CliError(Exception):
    """A runtime failure reported as a single ``error:`` line (exit status 1)."""


class OutputFormatError(ValueError):
    """The output file format could not be determined (a usage error)."""


class NoSupportedTLDError(CliError):
    """None of the requested TLDs has a registrar able to check it."""


def parse_tld_list(value: str) -> list[TLD]:
    """Parse ``"to,io,in"`` into TLDs: lowercased, deduplicated, order preserved.

    Raises ``argparse.ArgumentTypeError`` (an argparse usage error) when the list
    is empty or any suffix is not a valid TLD.
    """
    tlds: list[TLD] = []
    for raw in value.split(","):
        suffix = raw.strip().lstrip(".").lower()
        if not suffix:
            continue
        try:
            tld = TLD(suffix)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid TLD: {raw.strip()!r}") from exc
        if tld not in tlds:
            tlds.append(tld)
    if not tlds:
        raise argparse.ArgumentTypeError("at least one TLD is required")
    return tlds


def _selected_tlds(args: argparse.Namespace) -> list[TLD]:
    """The TLDs requested on the command line (accepts a raw string for convenience)."""
    value: str | list[TLD] = args.tld
    return parse_tld_list(value) if isinstance(value, str) else list(value)


def _non_negative_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
    if not 0 <= number < float("inf"):  # also rejects nan
        raise argparse.ArgumentTypeError(f"must be a finite number >= 0, got {value!r}")
    return number


def _non_negative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
    if number < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {value!r}")
    return number


def _positive_int(value: str) -> int:
    number = _non_negative_int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {value!r}")
    return number


def _range_max(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
    if not 1 <= number <= MAX_RANGE_LENGTH:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_RANGE_LENGTH}, got {value!r}")
    return number


def _range_end(value: str) -> str:
    if not _RANGE_END_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"must be lowercase letters a-z, got {value!r}")
    return value


def _encoding(value: str) -> str:
    try:
        return codecs.lookup(value).name
    except LookupError:
        raise argparse.ArgumentTypeError(f"unknown encoding: {value!r}") from None


def _contact(value: str) -> str:
    # Printable ASCII only: the value goes verbatim into an HTTP header.
    if not (value.isascii() and value.isprintable() and _EMAIL_RE.fullmatch(value)):
        raise argparse.ArgumentTypeError(f"not an email address: {value!r}")
    return value


def _add_encoding_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--encoding",
        type=_encoding,
        default=DEFAULT_ENCODING,
        help="Word list encoding (default: utf-8); undecodable bytes are an error",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="domainhack",
        description="Find domain hacks hiding in real words.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--tld",
        type=parse_tld_list,
        default="to",
        metavar="TLD[,TLD...]",
        help="TLD suffix, or a comma-separated list like to,io,in (default: to)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # filter subcommand
    filt = sub.add_parser("filter", help="Filter words that form domain hacks")
    filt.add_argument("file", type=Path, help="Path to word list file")
    filt.add_argument("--min-length", type=_non_negative_int, default=0, help="Minimum word length")
    _add_encoding_option(filt)

    # check subcommand
    chk = sub.add_parser(
        "check",
        help="Check domain availability",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    chk.add_argument(
        "--delay",
        type=_non_negative_float,
        default=1.0,
        help="Seconds between requests (default: 1.0)",
    )
    chk.add_argument("--show-taken", action="store_true", help="Also print taken domains")
    chk.add_argument(
        "--show-dropping",
        action="store_true",
        help="Also print taken domains in redemption or pending delete, which may "
        "soon be free (implied by --show-taken)",
    )
    chk.add_argument("--dry-run", action="store_true", help="List domains without checking")
    chk.add_argument(
        "--no-progress",
        action="store_true",
        help="Hide the progress bar (auto-hidden when stderr is not a TTY)",
    )

    source_group = chk.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--file", type=Path, help="Word list file (list mode)")
    source_group.add_argument(
        "--range-max",
        type=_range_max,
        help=f"Max combination length, 1-{MAX_RANGE_LENGTH} (range mode)",
    )
    chk.add_argument("--range-end", type=_range_end, default=None, help="Stop at this combination")
    chk.add_argument(
        "--order",
        choices=[o.value for o in CandidateOrder],
        default=None,
        help="Check order in list mode: score = shortest SLD, then shortest word, then "
        "alphabetical (default); alpha = alphabetical; input = word-list order. "
        "Range mode is always generated shortest-first (input)",
    )
    chk.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Check at most N domains per TLD, taken in --order",
    )
    chk.add_argument(
        "--parallel",
        type=_positive_int,
        default=DEFAULT_PARALLEL,
        metavar="N",
        help="Check up to N registry hosts at once, never more than one request per "
        f"host (default: {DEFAULT_PARALLEL}); results print as they complete. "
        "--parallel 1 checks one domain at a time, in order",
    )
    chk.add_argument(
        "--keep-order",
        action="store_true",
        help="With --parallel > 1, print and save results in check order "
        "instead of completion order",
    )
    chk.add_argument(
        "--yes",
        action="store_true",
        help=f"Run a brute-force check estimated at more than {GUARDRAIL_MAX_QUERIES:,} queries",
    )
    _add_encoding_option(chk)
    chk.add_argument("--output", type=Path, default=None, help="Also save results to FILE")
    chk.add_argument(
        "--format",
        choices=OUTPUT_FORMATS,
        default=None,
        help="Output file format (default: inferred from --output extension)",
    )
    chk.add_argument("--no-cache", action="store_true", help="Disable the result cache")
    chk.add_argument(
        "--cache-ttl",
        type=_non_negative_float,
        default=None,
        metavar="HOURS",
        help="Never reuse cached results older than HOURS (default: no cap). Without it, "
        "taken names are kept until their expiration date (at most 90 days; 30 days "
        "when unknown), and dropping names for 24 hours",
    )
    chk.add_argument(
        "--cache-ttl-available",
        type=_non_negative_float,
        default=AVAILABLE_TTL_SECONDS / 3600,
        metavar="HOURS",
        help="Reuse cached AVAILABLE results younger than HOURS "
        f"(default: {AVAILABLE_TTL_SECONDS / 3600:g})",
    )
    chk.add_argument(
        "--cache-path",
        type=Path,
        default=None,
        help="SQLite cache file (default: $XDG_CACHE_HOME/domainhack/results.sqlite3)",
    )
    chk.add_argument(
        "--contact",
        type=_contact,
        default=os.environ.get(CONTACT_ENV) or None,
        metavar="EMAIL",
        help="Send EMAIL as the HTTP From header on RDAP requests so registry "
        f"operators can reach you (default: ${CONTACT_ENV})",
    )

    return parser


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Cross-option checks argparse cannot express; exits with status 2 on error."""
    if args.command != "check":
        return
    if args.range_max is not None and args.order not in (None, CandidateOrder.INPUT.value):
        parser.error(
            f"--order {args.order} needs --file; range mode is generated shortest-first "
            "and too large to sort"
        )
    if args.range_end is None:
        return
    if args.range_max is None:
        parser.error("--range-end requires --range-max")
    if len(args.range_end) > args.range_max:
        parser.error(f"--range-end {args.range_end!r} is longer than --range-max {args.range_max}")


def _word_source(args: argparse.Namespace, path: Path) -> FileWordSource:
    return FileWordSource(path, encoding=getattr(args, "encoding", DEFAULT_ENCODING))


def cmd_filter(args: argparse.Namespace) -> int:
    """Print matching words.

    With a single TLD the output is one word per line (unchanged, pipeable into
    ``check --file``). With several TLDs each match is printed as
    ``word -> sld.tld``, one line per (word, TLD) match, since a word can match
    more than one TLD.
    """
    tlds = _selected_tlds(args)
    source = _word_source(args, args.file)
    use_case = FilterWordsUseCase(source, tlds, min_length=args.min_length)
    multi = len(tlds) > 1
    for hack in use_case.execute():
        print(f"{hack.word} -> {hack.display}" if multi else hack.word)
    _warn_skipped(use_case.skipped)
    return EXIT_OK


def _warn_skipped(count: int) -> None:
    """One stderr line about candidates dropped by label validation."""
    if count:
        print(f"skipped {count} invalid candidates", file=sys.stderr)


def _build_candidates(
    args: argparse.Namespace, tlds: TLD | Sequence[TLD]
) -> FilterWordsUseCase | RangeCandidatesUseCase:
    """Candidate source: (word, TLD) matches in list mode, SLD x TLD in range mode.

    Invalid labels are skipped and counted in the returned use case's ``skipped``.
    """
    tld_list: tuple[TLD, ...] = (tlds,) if isinstance(tlds, TLD) else tuple(tlds)
    if args.file:
        return FilterWordsUseCase(_word_source(args, args.file), tld_list)
    range_source = RangeWordSource(args.range_max, end_at=args.range_end)
    return RangeCandidatesUseCase(range_source, tld_list)


def _order(args: argparse.Namespace) -> CandidateOrder:
    """``--order``; by default ``score`` in list mode and ``input`` in range mode."""
    value: str | None = getattr(args, "order", None)
    if value is not None:
        return CandidateOrder(value)
    return CandidateOrder.SCORE if args.file else CandidateOrder.INPUT


def _build_ranker(args: argparse.Namespace, tlds: Sequence[TLD]) -> RankCandidatesUseCase:
    return RankCandidatesUseCase(_order(args), limit=getattr(args, "limit", None), tlds=tlds)


def _build_domains(args: argparse.Namespace, tlds: TLD | Sequence[TLD]) -> Iterable[DomainHack]:
    """Domains to check, in check order and capped by ``--limit`` per TLD.

    List mode yields every (word, TLD) match, best first by default. Range
    mode is lazy and SLD-major (a.to, a.io, b.to, b.io, ...).
    """
    tld_list: tuple[TLD, ...] = (tlds,) if isinstance(tlds, TLD) else tuple(tlds)
    return _build_ranker(args, tld_list).execute(_build_candidates(args, tld_list).execute())


def _resolve_output_format(output: Path, fmt: str | None) -> str:
    """Return the explicit format, or infer it from the output file extension."""
    if fmt is not None:
        return fmt
    inferred = _FORMAT_BY_SUFFIX.get(output.suffix.lower())
    if inferred is None:
        raise OutputFormatError(
            f"Cannot infer output format from {output.name!r}; "
            "use a .csv/.json/.jsonl extension or pass --format {csv,json}"
        )
    return inferred


def _build_writer(args: argparse.Namespace) -> ResultWriter:
    console = ConsoleResultWriter(
        show_taken=args.show_taken, show_dropping=getattr(args, "show_dropping", False)
    )
    output: Path | None = getattr(args, "output", None)
    if output is None:
        return console
    fmt = _resolve_output_format(output, getattr(args, "format", None))
    try:
        file_writer: ResultWriter = (
            CsvResultWriter(output) if fmt == "csv" else JsonResultWriter(output)
        )
    except OSError as exc:
        raise CliError(f"cannot write output file '{output}': {exc.strerror or exc}") from exc
    return CompositeResultWriter([console, file_writer])


def _range_totals(args: argparse.Namespace, tlds: Sequence[TLD]) -> dict[TLD, int]:
    """Range mode: exact candidates per TLD, capped by ``--limit``."""
    range_source = RangeWordSource(args.range_max, end_at=args.range_end)
    totals = RangeCandidatesUseCase(range_source, tlds).totals_by_tld()
    limit: int | None = getattr(args, "limit", None)
    if limit is not None:
        totals = {tld: min(count, limit) for tld, count in totals.items()}
    return totals


def _progress_total(args: argparse.Namespace, tlds: Sequence[TLD] | None = None) -> int | None:
    """Exact total in range mode (valid SLD x TLD pairs, capped by ``--limit``).

    None (indeterminate bar) in file mode: the total is only known once the
    list has been read (``cmd_check`` counts it when ``--order`` sorts it).
    """
    if args.file:
        return None
    tld_list = list(tlds) if tlds is not None else _selected_tlds(args)
    return sum(_range_totals(args, tld_list).values())


def _build_progress(args: argparse.Namespace) -> ProgressReporter:
    if getattr(args, "no_progress", False):
        return NullProgressReporter()
    return TqdmProgressReporter()


def _registrar_factory(
    args: argparse.Namespace, catalog: RegistrarCatalog | None = None
) -> RegistrarFactory:
    """Map a TLD to a fresh RegistrarClient, or None when no registrar supports it."""
    build: RegistrarCatalog = catalog if catalog is not None else build_registrar_for
    delay: float = args.delay
    contact: str | None = getattr(args, "contact", None)
    # One breaker per run: a host that stops answering is skipped for every TLD it serves.
    breaker = HostCircuitBreaker()

    def factory(tld: TLD) -> RegistrarClient | None:
        return build(tld, delay=delay, breaker=breaker, contact=contact)

    return factory


def _build_router(
    args: argparse.Namespace, catalog: RegistrarCatalog | None = None
) -> RegistrarRouter:
    return RegistrarRouter(_registrar_factory(args, catalog))


def _build_registrar(
    args: argparse.Namespace,
    router: RegistrarRouter | None = None,
    *,
    catalog: RegistrarCatalog | None = None,
) -> RegistrarClient:
    """The TLD router, wrapped in the result cache unless ``--no-cache``.

    The cache is keyed by fqdn, so a single cache serves every TLD.
    """
    registrar: RegistrarClient = router if router is not None else _build_router(args, catalog)
    if getattr(args, "no_cache", False):
        return registrar
    return CachedRegistrarClient(
        registrar, path=getattr(args, "cache_path", None), ttl=_cache_ttl_policy(args)
    )


def _lane_key(args: argparse.Namespace, router: RegistrarRouter) -> LaneKey:
    """One lane per registry host (see ``pacing.lane_for``), resolved once per TLD."""
    lanes: dict[TLD, Hashable] = {}

    def lane(domain: DomainHack) -> Hashable:
        tld = domain.tld
        if tld not in lanes:
            lanes[tld] = lane_for(router.client_for(tld), tld)
        return lanes[tld]

    return lane


def _cache_ttl_policy(args: argparse.Namespace) -> CacheTtlPolicy:
    """``--cache-ttl-available`` sets the AVAILABLE TTL; ``--cache-ttl`` caps every TTL."""
    cap_hours: float | None = getattr(args, "cache_ttl", None)
    available_hours: float = getattr(args, "cache_ttl_available", AVAILABLE_TTL_SECONDS / 3600)
    return CacheTtlPolicy(
        available=available_hours * 3600,
        cap=None if cap_hours is None else cap_hours * 3600,
    )


def _estimate_range_run(
    args: argparse.Namespace, router: RegistrarRouter, tlds: Sequence[TLD]
) -> RunEstimate:
    delay: float = args.delay

    def pacing(tld: TLD) -> Pacing:
        return pacing_for(router.client_for(tld), tld, delay)

    return estimate_run(_range_totals(args, tlds), pacing)


def _range_guardrail(
    args: argparse.Namespace, router: RegistrarRouter, tlds: Sequence[TLD]
) -> int | None:
    """Print the cost of a brute-force run; refuse a large one without ``--yes``.

    Returns EXIT_USAGE when the run is refused, None when it may go ahead.
    """
    estimate = _estimate_range_run(args, router, tlds)
    hosts = ", ".join(h.host for h in estimate.hosts)
    count = len(estimate.hosts)
    parallel: int = getattr(args, "parallel", 1)
    lanes = min(parallel, count)
    # The estimate is the busiest host's share: a lower bound whether hosts
    # are checked in parallel lanes or take turns (each host is paced on its own).
    how = f"; {lanes} hosts in parallel, one request at a time each" if lanes > 1 else ""
    _stderr(
        f"Estimated {estimate.queries:,} queries to {count} host{'s' if count != 1 else ''} "
        f"({hosts}): at least {format_duration(estimate.seconds)} at the current pacing{how}."
    )
    if not estimate.exceeds() or getattr(args, "yes", False):
        return None
    _stderr(
        f"error: refusing to send more than {GUARDRAIL_MAX_QUERIES:,} queries without --yes; "
        "registries' terms of use forbid bulk querying. Narrow the run with --range-end "
        "or --limit, preview it with --dry-run, or pass --yes."
    )
    return EXIT_USAGE


def _supported_tlds(router: RegistrarRouter, tlds: Sequence[TLD]) -> list[TLD]:
    """Keep TLDs the router can check; warn on stderr about the others.

    Raises NoSupportedTLDError when none is supported.
    """
    supported = [tld for tld in tlds if router.supports(tld)]
    unsupported = [f".{tld.suffix}" for tld in tlds if tld not in supported]
    if unsupported and supported:
        print(
            f"warning: no registrar supports {', '.join(unsupported)}; skipping",
            file=sys.stderr,
        )
    if not supported:
        router.close()
        raise NoSupportedTLDError(f"no registrar supports {', '.join(unsupported)}")
    return supported


def cmd_check(args: argparse.Namespace, *, catalog: RegistrarCatalog | None = None) -> int:
    """Check availability; ``catalog`` defaults to ``build_registrar_for``."""
    tlds = _selected_tlds(args)

    if args.dry_run:
        # The same candidates, order and limit as a real run.
        candidates = _build_candidates(args, tlds)
        for domain in _build_ranker(args, tlds).execute(candidates.execute()):
            print(f"  {domain.display}  (word: {domain.word!r})")
        _warn_skipped(candidates.skipped)
        return EXIT_OK

    # Fail fast on local problems, before any network work starts.
    if getattr(args, "output", None) is not None:
        _resolve_output_format(args.output, getattr(args, "format", None))
    if args.file:
        _word_source(args, args.file).check_readable()

    router = _build_router(args, catalog)
    tlds = _supported_tlds(router, tlds)
    try:
        refused = None if args.file else _range_guardrail(args, router, tlds)
        if refused is not None:
            router.close()
            return refused
        candidates = _build_candidates(args, tlds)
        ranker = _build_ranker(args, tlds)
        domains: Iterable[DomainHack] = ranker.execute(candidates.execute())
        total = _progress_total(args, tlds)
        if ranker.materializes:
            # Sorting reads the whole list anyway (before any output file is
            # opened); counting it gives the progress bar an exact total.
            domains = list(domains)
            total = len(domains)
        writer = _build_writer(args)
    except BaseException:
        router.close()
        raise

    with _build_registrar(args, router) as registrar:
        progress = _build_progress(args)
        # Workers call the router directly; the cache is read and written on
        # this thread only, so cache hits never wait for a lane.
        cache = registrar if isinstance(registrar, CachedRegistrarClient) else None
        use_case = CheckDomainsUseCase(
            router,
            writer,
            progress,
            cache=cache,
            parallel=getattr(args, "parallel", 1),
            lane_key=_lane_key(args, router),
            keep_order=getattr(args, "keep_order", False),
        )
        summary = use_case.execute(domains, total=total)
    _warn_skipped(candidates.skipped)
    return _report(summary)


def _report(summary: CheckSummary) -> int:
    """Print the run summary on stderr and map it to an exit status."""
    if summary.interrupted:
        _stderr(
            f"Interrupted after {summary.checked} checks "
            f"({summary.available} available, {summary.errors} errors)."
        )
        return EXIT_INTERRUPTED
    dropping = f" ({summary.dropping} dropping)" if summary.dropping else ""
    _stderr(
        f"\nDone. Checked {summary.checked} domains: {summary.available} available, "
        f"{summary.taken} taken{dropping}, {summary.errors} errors."
    )
    return EXIT_FAILURE if summary.errors else EXIT_OK


def _stderr(message: str) -> None:
    print(message, file=sys.stderr)


def _silence_stdout() -> None:
    """After EPIPE (``domainhack ... | head``), keep Python's exit-time flush quiet."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError):
        pass


def main(argv: Sequence[str] | None = None, *, catalog: RegistrarCatalog | None = None) -> int:
    """Run the CLI and return its exit status (see the ``--help`` epilog).

    ``catalog`` replaces ``build_registrar_for`` (the registrar catalog) for ``check``.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        _validate_args(parser, args)
    except SystemExit as exc:  # usage error (2), or --help / --version (0)
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE

    try:
        if args.command == "filter":
            return cmd_filter(args)
        return cmd_check(args, catalog=catalog)
    except OutputFormatError as exc:
        parser.print_usage(sys.stderr)
        _stderr(f"{parser.prog}: error: {exc}")
        return EXIT_USAGE
    except KeyboardInterrupt:
        _stderr("Interrupted.")
        return EXIT_INTERRUPTED
    except BrokenPipeError:
        _silence_stdout()
        return EXIT_FAILURE
    except (CliError, WordListError) as exc:
        _stderr(f"error: {exc}")
        return EXIT_FAILURE
    except OSError as exc:
        where = f"{exc.filename}: " if exc.filename else ""
        _stderr(f"error: {where}{exc.strerror or exc}")
        return EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
