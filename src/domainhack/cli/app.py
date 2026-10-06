import argparse
from collections.abc import Iterable
from pathlib import Path

from domainhack.adapters.cached_registrar import CachedRegistrarClient
from domainhack.adapters.console_writer import ConsoleResultWriter
from domainhack.adapters.file_word_source import FileWordSource
from domainhack.adapters.tonic_registrar import TonicRegistrarClient
from domainhack.domain.entities import TLD, DomainHack
from domainhack.ports.registrar import RegistrarClient
from domainhack.usecases.check_domains import CheckDomainsUseCase
from domainhack.usecases.filter_words import FilterWordsUseCase
from domainhack.usecases.generate_range import RangeWordSource


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

    source_group = chk.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--file", type=Path, help="Word list file (list mode)")
    source_group.add_argument("--range-max", type=int, help="Max combination length (range mode)")
    chk.add_argument("--range-end", type=str, default=None, help="Stop at this combination")
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

    with _build_registrar(args) as registrar:
        writer = ConsoleResultWriter(show_taken=args.show_taken)
        CheckDomainsUseCase(registrar, writer).execute(domains)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    match args.command:
        case "filter":
            cmd_filter(args)
        case "check":
            cmd_check(args)


if __name__ == "__main__":
    main()
