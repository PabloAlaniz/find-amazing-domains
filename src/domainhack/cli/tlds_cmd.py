"""``domainhack tlds``: which TLDs this version can check, how, and why not.

Offline: it reads the bundled IANA TLD list, RDAP bootstrap snapshot and
registry data, so the answer is the same on every machine.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections import Counter
from collections.abc import Sequence

from domainhack.adapters.iana_tlds import IanaTldList
from domainhack.adapters.rdap_bootstrap import RdapBootstrap
from domainhack.adapters.registrar_catalog import Backend, describe_backend

EXIT_OK = 0
EXIT_USAGE = 2


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser], *, epilog: str) -> None:
    """Add the ``tlds`` subcommand to the CLI's subparsers."""
    parser = sub.add_parser(
        "tlds",
        help="Show which TLDs can be checked, and how (RDAP or WHOIS) or why not",
        description="List TLDs with the backend used to check them. Without arguments: "
        "a coverage summary plus every country-code TLD. Offline.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "only", nargs="*", metavar="TLD", help="Only these TLDs (e.g. cl es com.ar)"
    )
    parser.add_argument(
        "--unsupported", action="store_true", help="Only TLDs that cannot be checked, with why"
    )
    parser.add_argument(
        "--all", action="store_true", help="Include generic TLDs (com, app...), not only ccTLDs"
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")


def _offline_bootstrap() -> RdapBootstrap:
    def no_fetch() -> bytes:
        raise OSError("offline")

    return RdapBootstrap(fetcher=no_fetch, warn=lambda _message: None)


def cmd_tlds(args: argparse.Namespace, bootstrap: RdapBootstrap | None = None) -> int:
    rdap = bootstrap if bootstrap is not None else _offline_bootstrap()
    known = IanaTldList()
    if args.only:
        suffixes = [s.lower().lstrip(".") for s in args.only]
        unknown = [s for s in suffixes if not known.is_known(s)]
        if unknown:
            print(f"domainhack tlds: error: not a known TLD: {', '.join(unknown)}", file=sys.stderr)
            return EXIT_USAGE
    else:
        suffixes = sorted(
            t
            for t in known.top_level_domains
            if not t.startswith("xn--") and (args.all or len(t) == 2)
        )
    every = [describe_backend(s, rdap) for s in suffixes]
    shown = [b for b in every if b.kind == "unsupported"] if args.unsupported else every
    if args.json:
        print(json.dumps([dataclasses.asdict(b) for b in shown], indent=2))
        sys.stdout.flush()  # surface a closed pipe (| head) to main's handler
        return EXIT_OK
    if not args.only:
        print(_summary(every))
        print()
    _print_table(shown)
    sys.stdout.flush()  # surface a closed pipe (| head) to main's handler
    return EXIT_OK


def _summary(backends: Sequence[Backend]) -> str:
    counts = Counter(b.kind for b in backends)
    supported = len(backends) - counts["unsupported"]
    parts = [
        f"{counts[k]} {k}" for k in ("rdap", "whois", "whois-fallback", "unsupported") if counts[k]
    ]
    return f"{supported} of {len(backends)} TLDs can be checked ({', '.join(parts)})"


def _print_table(backends: Sequence[Backend]) -> None:
    width_tld = max((len(b.tld) for b in backends), default=3) + 1
    width_kind = max((len(b.kind) for b in backends), default=4)
    width_host = max((len(b.host) for b in backends), default=4)
    for b in backends:
        print(f"  .{b.tld:<{width_tld}} {b.kind:<{width_kind}}  {b.host:<{width_host}}  {b.detail}")
