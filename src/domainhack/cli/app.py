import argparse
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

from domainhack.adapters._circuit import HostCircuitBreaker
from domainhack.adapters.cached_registrar import CachedRegistrarClient
from domainhack.adapters.composite_writer import CompositeResultWriter
from domainhack.adapters.console_writer import ConsoleResultWriter
from domainhack.adapters.csv_writer import CsvResultWriter
from domainhack.adapters.file_word_source import FileWordSource
from domainhack.adapters.json_writer import JsonResultWriter
from domainhack.adapters.registrar_catalog import build_registrar_for
from domainhack.adapters.registrar_router import RegistrarFactory, RegistrarRouter
from domainhack.adapters.tqdm_progress import TqdmProgressReporter
from domainhack.domain.entities import TLD, DomainHack
from domainhack.ports.progress import NullProgressReporter, ProgressReporter
from domainhack.ports.registrar import RegistrarClient
from domainhack.ports.result_writer import ResultWriter
from domainhack.usecases.check_domains import CheckDomainsUseCase
from domainhack.usecases.filter_words import FilterWordsUseCase
from domainhack.usecases.generate_range import RangeWordSource

OUTPUT_FORMATS = ("csv", "json")
_FORMAT_BY_SUFFIX = {".csv": "csv", ".json": "json", ".jsonl": "json"}


class OutputFormatError(ValueError):
    """The output file format could not be determined."""


class NoSupportedTLDError(ValueError):
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="domainhack",
        description="Find domain hacks hiding in real words.",
    )
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
    filt.add_argument("--min-length", type=int, default=0, help="Minimum word length")

    # check subcommand
    chk = sub.add_parser("check", help="Check domain availability")
    chk.add_argument(
        "--delay", type=float, default=1.0, help="Seconds between requests (default: 1.0)"
    )
    chk.add_argument("--show-taken", action="store_true", help="Also print taken domains")
    chk.add_argument("--dry-run", action="store_true", help="List domains without checking")
    chk.add_argument(
        "--no-progress",
        action="store_true",
        help="Hide the progress bar (auto-hidden when stderr is not a TTY)",
    )

    source_group = chk.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--file", type=Path, help="Word list file (list mode)")
    source_group.add_argument("--range-max", type=int, help="Max combination length (range mode)")
    chk.add_argument("--range-end", type=str, default=None, help="Stop at this combination")
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
        type=float,
        default=168.0,
        metavar="HOURS",
        help="Reuse cached results younger than HOURS (default: 168 = 7 days)",
    )
    chk.add_argument(
        "--cache-path",
        type=Path,
        default=None,
        help="SQLite cache file (default: $XDG_CACHE_HOME/domainhack/results.sqlite3)",
    )

    return parser


def cmd_filter(args: argparse.Namespace) -> None:
    """Print matching words.

    With a single TLD the output is one word per line (unchanged, pipeable into
    ``check --file``). With several TLDs each match is printed as
    ``word -> sld.tld``, one line per (word, TLD) match, since a word can match
    more than one TLD.
    """
    tlds = _selected_tlds(args)
    source = FileWordSource(args.file)
    use_case = FilterWordsUseCase(source, tlds, min_length=args.min_length)
    multi = len(tlds) > 1
    for hack in use_case.execute():
        print(f"{hack.word} -> {hack.fqdn}" if multi else hack.word)


def _build_domains(args: argparse.Namespace, tlds: TLD | Sequence[TLD]) -> Iterable[DomainHack]:
    """Domains to check: every (word, TLD) match in list mode, every SLD x TLD in range mode.

    Range mode is SLD-major (a.to, a.io, b.to, b.io, ...).
    """
    tld_list: tuple[TLD, ...] = (tlds,) if isinstance(tlds, TLD) else tuple(tlds)
    if args.file:
        word_source = FileWordSource(args.file)
        return FilterWordsUseCase(word_source, tld_list).execute()
    range_source = RangeWordSource(args.range_max, end_at=args.range_end)
    return (DomainHack.from_sld(sld, tld) for sld in range_source.words() for tld in tld_list)


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
    console = ConsoleResultWriter(show_taken=args.show_taken)
    output: Path | None = getattr(args, "output", None)
    if output is None:
        return console
    fmt = _resolve_output_format(output, getattr(args, "format", None))
    file_writer: ResultWriter = (
        CsvResultWriter(output) if fmt == "csv" else JsonResultWriter(output)
    )
    return CompositeResultWriter([console, file_writer])


def _progress_total(args: argparse.Namespace, tlds: Sequence[TLD] | None = None) -> int | None:
    """Exact total in range mode (SLDs x TLDs); None (indeterminate bar) in file mode."""
    if args.file:
        return None
    tld_count = len(tlds) if tlds is not None else len(_selected_tlds(args))
    return RangeWordSource(args.range_max, end_at=args.range_end).total() * tld_count


def _build_progress(args: argparse.Namespace) -> ProgressReporter:
    if getattr(args, "no_progress", False):
        return NullProgressReporter()
    return TqdmProgressReporter()


def _registrar_factory(args: argparse.Namespace) -> RegistrarFactory:
    """Map a TLD to a fresh RegistrarClient, or None when no registrar supports it."""
    delay: float = args.delay
    # One breaker per run: a host that stops answering is skipped for every TLD it serves.
    breaker = HostCircuitBreaker()

    def factory(tld: TLD) -> RegistrarClient | None:
        return build_registrar_for(tld, delay=delay, breaker=breaker)

    return factory


def _build_router(args: argparse.Namespace) -> RegistrarRouter:
    return RegistrarRouter(_registrar_factory(args))


def _build_registrar(
    args: argparse.Namespace, router: RegistrarRouter | None = None
) -> RegistrarClient:
    """The TLD router, wrapped in the result cache unless ``--no-cache``.

    The cache is keyed by fqdn, so a single cache serves every TLD.
    """
    registrar: RegistrarClient = router if router is not None else _build_router(args)
    if getattr(args, "no_cache", False):
        return registrar
    return CachedRegistrarClient(
        registrar,
        path=getattr(args, "cache_path", None),
        ttl_seconds=getattr(args, "cache_ttl", 168.0) * 3600,
    )


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


def cmd_check(args: argparse.Namespace) -> None:
    tlds = _selected_tlds(args)

    if args.dry_run:
        for domain in _build_domains(args, tlds):
            print(f"  {domain.fqdn}  (word: {domain.word!r})")
        return

    if getattr(args, "output", None) is not None:
        # Validate the output format before any network work starts.
        _resolve_output_format(args.output, getattr(args, "format", None))

    router = _build_router(args)
    tlds = _supported_tlds(router, tlds)
    domains = _build_domains(args, tlds)

    with _build_registrar(args, router) as registrar:
        writer = _build_writer(args)
        progress = _build_progress(args)
        CheckDomainsUseCase(registrar, writer, progress).execute(
            domains, total=_progress_total(args, tlds)
        )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    match args.command:
        case "filter":
            cmd_filter(args)
        case "check":
            try:
                cmd_check(args)
            except (OutputFormatError, NoSupportedTLDError) as exc:
                parser.error(str(exc))


if __name__ == "__main__":
    main()
