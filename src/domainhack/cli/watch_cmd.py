"""``domainhack watch``: keep a list of names and report what changed since last time.

    domainhack watch add sumanda.com pla.to
    domainhack watch run          # check them all, print only the changes
    domainhack watch list
    domainhack watch remove pla.to

``watch run`` asks the registries afresh (the result cache is not read),
compares each answer with the one stored in the watchlist and prints one line
per change: a name became available, started dropping, was registered or
restored, its expiry date moved, its parking service changed. Run it from
cron; with ``--exit-code`` it exits 3 when something changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from domainhack.adapters._registration import format_utc
from domainhack.adapters.config_file import Config
from domainhack.adapters.registry_sources import unsupported_message
from domainhack.adapters.watch_store import WatchEntry, WatchlistError, WatchStore
from domainhack.cli import app
from domainhack.cli.checks import check_names, parse_domains
from domainhack.usecases.watch import WatchEvent, WatchState, compare, state_of

EXIT_CHANGED = 3
WATCH_FORMATS = ("text", "json")
Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def register(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    epilog: str,
    config: Config | None = None,
) -> None:
    """Add the ``watch`` subcommand (and its own subcommands) to the CLI's subparsers."""
    config = config if config is not None else Config()
    parser = sub.add_parser(
        "watch",
        help="Keep a list of names and report what changed since the last check",
        description="Watch names you care about. 'watch run' checks them and prints only "
        "what changed since the last run: became available, dropping, registered, "
        "restored, expiry date moved, parking changed.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = parser.add_subparsers(dest="watch_action", required=True)

    def action(name: str, help_text: str) -> argparse.ArgumentParser:
        child = actions.add_parser(name, help=help_text, description=help_text)
        child.add_argument(
            "--watchlist",
            type=Path,
            default=None,
            metavar="FILE",
            help="Watchlist file (default: $DOMAINHACK_WATCHLIST, then "
            "$XDG_DATA_HOME/domainhack/watchlist.json)",
        )
        app.add_config_options(child)
        return child

    add = action("add", "Start watching full domain names (pla.to, foto.com.ar)")
    add.add_argument("names", nargs="+", metavar="DOMAIN")
    remove = action("remove", "Stop watching names")
    remove.add_argument("names", nargs="+", metavar="DOMAIN")
    listing = action("list", "Show the watched names and what was last seen")
    listing.add_argument("--json", action="store_true", help="Machine-readable output")
    run = action("run", "Check every watched name and print what changed")
    run.add_argument(
        "--format",
        choices=WATCH_FORMATS,
        default="text",
        help="Output on stdout (default: text)",
    )
    run.add_argument(
        "--exit-code",
        action="store_true",
        help=f"Exit with status {EXIT_CHANGED} when something changed (for scripts and cron)",
    )
    app.add_run_options(run)
    app.apply_run_config(run, config)


def _names(count: int) -> str:
    return f"{count} name{'s' if count != 1 else ''}"


def _add(args: argparse.Namespace, store: WatchStore, catalog: app.RegistrarCatalog | None) -> int:
    domains, rejected = parse_domains(args.names)
    for item in rejected:
        app._stderr(f"error: {item['domain']}: {item['reason']}")
    entries = store.load()
    added = 0
    # Only asked which TLDs it supports; it sends nothing.
    router = app._build_router(argparse.Namespace(delay=1.0, contact=None), catalog)
    try:
        for domain in domains:
            if not router.supports(domain.tld):
                suffix = domain.tld.suffix
                reason = unsupported_message(suffix) or f"no registrar supports .{suffix}"
                app._stderr(f"error: {domain.display}: {reason}")
                rejected.append({"domain": domain.display, "reason": reason})
            elif domain.fqdn in entries:
                app._stderr(f"{domain.display} is already watched")
            else:
                entries[domain.fqdn] = WatchEntry(domain.fqdn, domain.display, format_utc(_now()))
                added += 1
    finally:
        router.close()
    if added:
        store.save(entries)
    app._stderr(f"Watching {_names(len(entries))} ({added} added).")
    return app.EXIT_USAGE if rejected else app.EXIT_OK


def _remove(args: argparse.Namespace, store: WatchStore) -> int:
    entries = store.load()
    domains, rejected = parse_domains(args.names)
    missing = [item["domain"] for item in rejected]
    removed = 0
    for domain in domains:
        if entries.pop(domain.fqdn, None) is None:
            missing.append(domain.display)
        else:
            removed += 1
    for name in missing:
        app._stderr(f"error: {name} is not watched")
    if removed:
        store.save(entries)
    app._stderr(f"Watching {_names(len(entries))} ({removed} removed).")
    return app.EXIT_USAGE if missing else app.EXIT_OK


def _entry_json(entry: WatchEntry) -> dict[str, Any]:
    return {
        "domain": entry.display,
        "fqdn": entry.fqdn,
        "added_at": entry.added_at,
        "checked_at": entry.checked_at or None,
        "state": _state_json(entry.state),
    }


def _state_json(state: WatchState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "status": state.status,
        "statuses": list(state.statuses),
        "expires_at": state.expires_at or None,
        "parked_hint": state.parked_hint,
    }


def _list(args: argparse.Namespace, store: WatchStore) -> int:
    entries = list(store.load().values())
    if args.json:
        print(json.dumps([_entry_json(e) for e in entries], indent=2, ensure_ascii=False))
        return app.EXIT_OK
    if not entries:
        app._stderr("No names are watched yet: domainhack watch add NAME.TLD")
        return app.EXIT_OK
    width = max(len(e.display) for e in entries)
    for entry in entries:
        seen = entry.state.describe() if entry.state is not None else "not checked yet"
        when = f"  (checked {entry.checked_at[:10]})" if entry.checked_at else ""
        print(f"{entry.display.ljust(width)}  {seen}{when}")
    return app.EXIT_OK


def _event_json(event: WatchEvent) -> dict[str, Any]:
    return {
        "domain": event.name,
        "kind": event.kind.value,
        "message": event.message,
        "before": _state_json(event.before),
        "after": _state_json(event.after),
    }


def _run(
    args: argparse.Namespace,
    store: WatchStore,
    catalog: app.RegistrarCatalog | None,
    clock: Clock,
) -> int:
    entries = store.load()
    if not entries:
        app._stderr("No names are watched yet: domainhack watch add NAME.TLD")
        return app.EXIT_OK
    domains, _ = parse_domains(list(entries))
    # A watch wants today's answer: cached results are not read (new ones are still stored).
    args.cache_ttl = 0.0
    results, summary, unsupported = check_names(domains, run=args, catalog=catalog, dns=None)

    checked_at = format_utc(clock())
    events: list[WatchEvent] = []
    failed: list[dict[str, str]] = [
        {
            "domain": entries[d.fqdn].display,
            "reason": unsupported_message(d.tld.suffix) or f"no registrar supports .{d.tld.suffix}",
        }
        for d in unsupported
    ]
    for result in results:
        entry = entries[result.domain.fqdn]
        state = state_of(result)
        if state is None:
            reason = result.error_message or "check failed"
            failed.append({"domain": entry.display, "reason": reason})
            continue
        events += compare(entry.display, entry.state, state)
        entries[entry.fqdn] = WatchEntry(
            entry.fqdn, entry.display, entry.added_at, checked_at, state
        )
    store.save(entries)

    changes = [e for e in events if e.is_change]
    if args.format == "json":
        data = {
            "checked": len(results) - sum(1 for r in results if state_of(r) is None),
            "changes": [_event_json(e) for e in changes],
            "first_checks": [_event_json(e) for e in events if not e.is_change],
            "not_checked": failed,
        }
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        for event in events:
            print(f"{event.name}: {event.message}")
    for item in failed:
        app._stderr(f"warning: {item['domain']} could not be checked: {item['reason']}")
    count = len(changes)
    app._stderr(
        f"Checked {_names(len(entries))}: {count} change{'s' if count != 1 else ''}, "
        f"{len(failed)} not checked."
    )
    if summary is not None and summary.interrupted:
        return app.EXIT_INTERRUPTED
    if changes and args.exit_code:
        return EXIT_CHANGED
    return app.EXIT_FAILURE if failed else app.EXIT_OK


def cmd_watch(
    args: argparse.Namespace,
    *,
    catalog: app.RegistrarCatalog | None = None,
    clock: Clock = _now,
) -> int:
    store = WatchStore(args.watchlist)
    try:
        if args.watch_action == "add":
            return _add(args, store, catalog)
        if args.watch_action == "remove":
            return _remove(args, store)
        if args.watch_action == "list":
            return _list(args, store)
        return _run(args, store, catalog, clock)
    except WatchlistError as exc:
        app._stderr(f"error: {exc}")
        return app.EXIT_FAILURE
    finally:
        sys.stdout.flush()
