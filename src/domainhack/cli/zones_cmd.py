"""``domainhack zones``: import a registry's zone file so its names need no query.

    dig @ZONE-SERVER nu AXFR > nu.zone      # see the registry's own instructions
    domainhack zones import nu nu.zone
    domainhack zones list
    domainhack zones remove nu

Once a TLD has a snapshot, ``check``, ``name`` and the MCP tools answer
"taken" for every name in it without asking the registry; names that are not
in it are checked live as always. A snapshot is used for a week, then ignored
until it is imported again.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from domainhack.adapters.zone_snapshot import ZONE_MAX_AGE_DAYS, ZoneError, ZoneStore
from domainhack.cli import app


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser], *, epilog: str) -> None:
    """Add the ``zones`` subcommand (and its own subcommands) to the CLI's subparsers."""
    parser = sub.add_parser(
        "zones",
        help="Import a registry's zone file so names in it are known to be taken without a query",
        description="Keep zone snapshots. A name in a snapshot is certainly registered, so it "
        "is answered offline; a name that is not in it is still checked at the registry. "
        f"Snapshots are used for {ZONE_MAX_AGE_DAYS} days.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = parser.add_subparsers(dest="zones_action", required=True)

    def action(name: str, help_text: str) -> argparse.ArgumentParser:
        child = actions.add_parser(name, help=help_text, description=help_text)
        child.add_argument(
            "--zone-dir",
            type=Path,
            default=None,
            metavar="DIR",
            help="Where snapshots are kept (default: $DOMAINHACK_ZONES, then "
            "$XDG_DATA_HOME/domainhack/zones)",
        )
        app.add_config_options(child)
        return child

    importing = action(
        "import", "Store the names delegated under TLD in FILE (dig AXFR output; gzip is fine)"
    )
    importing.add_argument("tld", metavar="TLD")
    importing.add_argument("file", type=Path, metavar="FILE")
    action("list", "Show the stored snapshots and whether they are still used")
    removing = action("remove", "Delete the snapshot of a TLD")
    removing.add_argument("tld", metavar="TLD")


def cmd_zones(args: argparse.Namespace, *, today: date | None = None) -> int:
    store = ZoneStore(args.zone_dir)
    now = today if today is not None else date.today()
    try:
        if args.zones_action == "import":
            info = store.import_zone(args.tld, args.file, now)
            app._stderr(f"Imported {info.names:,} names under .{info.suffix} into {info.path}")
            return app.EXIT_OK
        if args.zones_action == "remove":
            suffix = args.tld.lower().lstrip(".")
            if not store.remove(suffix):
                app._stderr(f"error: no snapshot for .{suffix}")
                return app.EXIT_USAGE
            app._stderr(f"Removed the snapshot of .{suffix}")
            return app.EXIT_OK
        infos = store.snapshots()
        if not infos:
            app._stderr("No zone snapshots yet: domainhack zones import TLD FILE")
            return app.EXIT_OK
        for info in infos:
            age = info.age_days(now)
            state = "in use" if info.is_fresh(now) else "too old, ignored: import it again"
            print(
                f".{info.suffix}  {info.names:,} names  imported {info.imported.isoformat()} "
                f"({age} day{'s' if age != 1 else ''} ago, {state})"
            )
        return app.EXIT_OK
    except ZoneError as exc:
        app._stderr(f"error: {exc}")
        return app.EXIT_FAILURE
