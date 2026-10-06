import argparse
from collections.abc import Iterable
from pathlib import Path

from domainhack.adapters.cached_registrar import CachedRegistrarClient
from domainhack.adapters.composite_writer import CompositeResultWriter
from domainhack.adapters.console_writer import ConsoleResultWriter
from domainhack.adapters.csv_writer import CsvResultWriter
from domainhack.adapters.file_word_source import FileWordSource
from domainhack.adapters.json_writer import JsonResultWriter
from domainhack.adapters.tonic_registrar import TonicRegistrarClient
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="domainhack",
        description="Find domain hacks hiding in real words.",
    )
    parser.add_argument("--tld", default="to", help="TLD suffix (default: to)")

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
    tld = TLD(args.tld)
    source = FileWordSource(args.file)
    use_case = FilterWordsUseCase(source, tld, min_length=args.min_length)
    for hack in use_case.execute():
        print(hack.word)


def _build_domains(args: argparse.Namespace, tld: TLD) -> Iterable[DomainHack]:
    if args.file:
        word_source = FileWordSource(args.file)
        return FilterWordsUseCase(word_source, tld).execute()
    range_source = RangeWordSource(args.range_max, end_at=args.range_end)
    return (DomainHack.from_sld(sld, tld) for sld in range_source.words())


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


def _progress_total(args: argparse.Namespace) -> int | None:
    """Exact total in range mode; None (indeterminate bar) in file mode."""
    if args.file:
        return None
    return RangeWordSource(args.range_max, end_at=args.range_end).total()


def _build_progress(args: argparse.Namespace) -> ProgressReporter:
    if getattr(args, "no_progress", False):
        return NullProgressReporter()
    return TqdmProgressReporter()


def _build_registrar(args: argparse.Namespace) -> RegistrarClient:
    registrar: RegistrarClient = TonicRegistrarClient(delay=args.delay)
    if getattr(args, "no_cache", False):
        return registrar
    return CachedRegistrarClient(
        registrar,
        path=getattr(args, "cache_path", None),
        ttl_seconds=getattr(args, "cache_ttl", 168.0) * 3600,
    )


def cmd_check(args: argparse.Namespace) -> None:
    tld = TLD(args.tld)
    domains = _build_domains(args, tld)

    if args.dry_run:
        for domain in domains:
            print(f"  {domain.fqdn}  (word: {domain.word!r})")
        return

    if getattr(args, "output", None) is not None:
        # Validate the output format before any network work starts.
        _resolve_output_format(args.output, getattr(args, "format", None))

    with _build_registrar(args) as registrar:
        writer = _build_writer(args)
        progress = _build_progress(args)
        CheckDomainsUseCase(registrar, writer, progress).execute(
            domains, total=_progress_total(args)
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
            except OutputFormatError as exc:
                parser.error(str(exc))


if __name__ == "__main__":
    main()
