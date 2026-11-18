"""domainhack as an MCP server, so an agent can find a domain while it names a project.

Six tools, built on the same use cases as the CLI:

- ``find_domain_for_name``: what ``domainhack name`` does, as structured data;
- ``check_domains``: availability of specific names, with registration details;
- ``check_pattern``: the names a pattern matches (``CVCV``), a bounded number per call;
- ``watch_domains``: the watchlist of ``domainhack watch`` (add, remove, list, run);
- ``find_hacks_for_words``: which words end in a TLD (offline, no network);
- ``list_tlds``: which TLDs can be checked, how, and why not (offline).

Run ``domainhack-mcp`` (stdio transport). It takes the run options the CLI
shares (``--delay``, ``--parallel``, ``--no-cache``, ``--cache-path``,
``--contact``) and reads the same configuration file (``--config``,
``--no-config``); the progress bar is always off because stdout carries the
protocol. Install with ``pip install "domainhack[mcp]"``.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import anyio

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError as exc:  # pragma: no cover - only without the extra installed
    raise ImportError(
        'the MCP server needs the "mcp" extra: pip install "domainhack[mcp]"'
    ) from exc

from domainhack import __version__
from domainhack.adapters.brand_data import DEFAULT_PRESET, load_tld_presets
from domainhack.adapters.brand_report_render import report_to_dict
from domainhack.adapters.config_file import Config, ConfigError
from domainhack.adapters.iana_tlds import IanaTldList
from domainhack.adapters.json_writer import result_record
from domainhack.adapters.rdap_bootstrap import RdapBootstrap
from domainhack.adapters.registrar_catalog import describe_backend
from domainhack.adapters.registry_sources import unsupported_message
from domainhack.adapters.watch_store import WatchlistError, WatchStore
from domainhack.cli import app
from domainhack.cli.checks import check_names, parse_domains
from domainhack.cli.name_cmd import (
    NameServices,
    NameUsageError,
    default_services,
    find_name,
    parse_affixes,
    validate_brand_name,
)
from domainhack.cli.tlds_cmd import offline_bootstrap
from domainhack.cli.watch_cmd import add_names, entry_json, remove_names, run_watch
from domainhack.domain.entities import (
    TLD,
    Availability,
    DomainCheckResult,
    DomainHack,
    InvalidLabelError,
)
from domainhack.usecases.brand_candidates import MAX_AFFIXES
from domainhack.usecases.check_domains import CheckSummary
from domainhack.usecases.generate_pattern import PatternError, PatternWordSource
from domainhack.usecases.generate_range import RangeCandidatesUseCase
from domainhack.usecases.rank_candidates import CandidateOrder, RankCandidatesUseCase

SERVER_NAME = "domainhack"

INSTRUCTIONS = """\
domainhack finds domain hacks (real words that end in a TLD: plato -> pla.to) and checks
domain availability directly against the registries over RDAP and WHOIS, with no API keys.

- To find a domain for a project or brand name, call find_domain_for_name with the bare
  name (no TLD). It checks the exact name under a TLD preset ("startup" by default), finds
  domain hacks that spell it, optionally tries brand variants, and returns a ranked
  recommendation. Only names with status "available" are recommended; "dns-conflict",
  "taken", "dropping" and "unverifiable" names are not free.
- To check specific names, call check_domains with full domain names.
- To look for a short invented name, call check_pattern (CVCV under "to" -> baba.to...).
- To wait for a taken name, call watch_domains: "add" it, then "run" from time to time;
  a run returns only what changed (became available, dropping, registered...).
- find_hacks_for_words and list_tlds are offline and instant.

Registries limit automated lookups: a call may take several seconds per TLD, results are
cached, and "available" means "not registered" (premium or reserved names may still show as
available). Confirm with a registrar before buying.
"""

TldKind = Literal["supported", "unsupported", "all"]
WatchAction = Literal["add", "remove", "list", "run"]
# Most names one check_pattern call sends to the registries, across its TLDs.
MAX_PATTERN_CHECKS = 100


def run_options(argv: Sequence[str] = ()) -> argparse.Namespace:
    """The CLI's shared run options (``app.add_run_options``) parsed from ``argv``.

    The progress bar is always off: with the stdio transport, stdout belongs
    to the protocol and stderr should stay quiet.
    """
    parser = argparse.ArgumentParser(
        prog="domainhack-mcp",
        description="Run domainhack as an MCP server over stdio.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    app.add_run_options(parser)
    app.add_config_options(parser, top_level=True)
    try:
        config = app.config_from_argv(list(argv))
    except ConfigError as exc:
        parser.error(str(exc))
    app.apply_run_config(parser, config)
    args = parser.parse_args(list(argv))
    args.no_progress = True
    args.config_data = config
    return args


def _summary_dict(summary: CheckSummary | None) -> dict[str, Any]:
    if summary is None:
        return {
            "checked": 0,
            "available": 0,
            "taken": 0,
            "dropping": 0,
            "errors": 0,
            "interrupted": False,
            "registries_with_errors": [],
        }
    return {
        "checked": summary.checked,
        "available": summary.available,
        "taken": summary.taken,
        "dropping": summary.dropping,
        "errors": summary.errors,
        "interrupted": summary.interrupted,
        "registries_with_errors": [
            {
                "tld": tally.suffix,
                "errors": tally.errors,
                "checked": tally.checked,
                "unreachable": tally.unreachable,
            }
            for tally in summary.tlds_with_errors
        ],
    }


def _is_free(result: DomainCheckResult) -> bool:
    return result.availability is Availability.AVAILABLE and not result.dns_conflict


def _unsupported_entry(domain: DomainHack) -> dict[str, str]:
    reason = unsupported_message(domain.tld.suffix) or f"no registrar supports .{domain.tld.suffix}"
    return {"domain": domain.display, "reason": reason}


def build_server(
    *,
    run: argparse.Namespace | None = None,
    catalog: app.RegistrarCatalog | None = None,
    services: NameServices | None = None,
    bootstrap: RdapBootstrap | None = None,
    watchlist: Path | None = None,
) -> MCPServer:
    """The MCP server with its six tools.

    Requests that cannot be served (invalid name, no valid TLD, no registrar
    for any candidate) raise ``ToolError``, which the client sees as a tool
    error with that message. ``run`` holds the shared run options (default:
    the CLI defaults, no progress bar). ``catalog`` and ``services`` replace the registrar catalog
    and the ``name`` services, as in the CLI (tests pass fakes). ``bootstrap``
    is the RDAP bootstrap ``list_tlds`` describes (default: the bundled
    snapshot, offline). ``watchlist`` is the file ``watch_domains`` uses
    (default: the CLI's own, see ``adapters/watch_store.py``).
    """
    run_opts = run if run is not None else run_options()
    svc = services if services is not None else default_services()
    rdap = bootstrap if bootstrap is not None else offline_bootstrap()
    config: Config = getattr(run_opts, "config_data", None) or Config()
    default_tlds = config.name_tlds or DEFAULT_PRESET
    presets = ", ".join(load_tld_presets(config.presets))

    server = MCPServer(SERVER_NAME, version=__version__, instructions=INSTRUCTIONS)

    @server.tool(
        name="find_domain_for_name",
        description=(
            "Find a domain for a project or brand name: checks the exact name under a set of "
            f"TLDs (presets: {presets}; or suffixes, mixed: 'startup,com.ar,la'), finds domain "
            "hacks that spell it (including ones with a subdomain, delicious -> "
            "del.icio.us), optionally brand variants, and returns a ranked "
            "recommendation with registration details for taken names. 'variants' tries "
            "get/use/try/hola/my + name and name + hq/app/labs/studio; 'prefixes' and "
            f"'suffixes' (at most {MAX_AFFIXES} each) are your own words instead. "
            "'handles' also says whether the name is taken on GitHub, npm and PyPI. "
            "Queries the registries live; may take several seconds."
        ),
    )
    async def find_domain_for_name(
        name: str,
        tlds: str = default_tlds,
        variants: bool = False,
        confirm_dns: bool = True,
        prefixes: list[str] | None = None,
        suffixes: list[str] | None = None,
        subdomain_hacks: bool = True,
        handles: bool = False,
    ) -> dict[str, Any]:
        clean_prefixes: Sequence[str] | None = None
        clean_suffixes: Sequence[str] | None = None
        try:
            clean = validate_brand_name(name)
            if prefixes:
                clean_prefixes = parse_affixes(",".join(prefixes))
            if suffixes:
                clean_suffixes = parse_affixes(",".join(suffixes))
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        if clean_prefixes is None and clean_suffixes is None and not variants:
            # The configuration file's own words apply only when the caller names none.
            clean_prefixes, clean_suffixes = config.name_prefixes, config.name_suffixes
        warnings: list[str] = []

        def work() -> dict[str, Any]:
            try:
                outcome = find_name(
                    clean,
                    tlds,
                    run=run_opts,
                    variants=variants,
                    prefixes=clean_prefixes,
                    suffixes=clean_suffixes,
                    subdomain_hacks=subdomain_hacks,
                    handles=handles,
                    presets=config.presets,
                    # True means "when a DNS lookup is available" (None to find_name).
                    confirm_dns=None if confirm_dns else False,
                    catalog=catalog,
                    services=svc,
                    warn=warnings.append,
                )
            except (NameUsageError, app.NoSupportedTLDError) as exc:
                raise ToolError(str(exc)) from None
            data = report_to_dict(outcome.report)
            data["summary"] = _summary_dict(outcome.summary)
            if outcome.skipped:
                warnings.append(f"skipped {outcome.skipped} invalid candidates")
            data["warnings"] = warnings
            return data

        return await anyio.to_thread.run_sync(work)

    @server.tool(
        name="check_domains",
        description=(
            "Check whether specific domain names are registered, asking each registry "
            "directly (RDAP or WHOIS). Taken names come with statuses, registration and "
            "expiry dates, registrar, nameservers and parking hint; 'dns_conflict' marks "
            "names the registry calls available but that have nameservers in DNS. "
            "Queries the registries live."
        ),
    )
    async def check_domains(domains: list[str], confirm_dns: bool = True) -> dict[str, Any]:
        parsed, rejected = parse_domains(domains)
        if not parsed:
            reasons = "; ".join(f"{r['domain']}: {r['reason']}" for r in rejected)
            raise ToolError(f"no valid domain name in the request ({reasons})")
        dns = svc.dns if confirm_dns else None

        def work() -> dict[str, Any]:
            results, summary, unsupported = check_names(
                parsed, run=run_opts, catalog=catalog, dns=dns
            )
            return {
                "results": [result_record(r) for r in results],
                "unsupported": [_unsupported_entry(d) for d in unsupported],
                "rejected": rejected,
                "summary": _summary_dict(summary),
            }

        return await anyio.to_thread.run_sync(work)

    @server.tool(
        name="check_pattern",
        description=(
            "Check short, pronounceable names that fit a pattern, under one or more TLDs. "
            "One pattern character per letter of the name: C = consonant, V = vowel, "
            "L = any letter, D = digit; lowercase letters, digits and hyphens stand for "
            "themselves (CVCV -> baba, babe...; goCV -> goba...). Names are checked in "
            f"order, at most 'limit' per TLD and {MAX_PATTERN_CHECKS} in the whole call; "
            "'total' says how many names fit, so a narrower pattern (more literals) is the "
            "way to reach the rest. Queries the registries live."
        ),
    )
    async def check_pattern(
        pattern: str,
        tlds: list[str],
        limit: int = 20,
        confirm_dns: bool = False,
    ) -> dict[str, Any]:
        try:
            source = PatternWordSource(pattern)
        except PatternError as exc:
            raise ToolError(f"invalid pattern {pattern!r}: {exc}") from None
        try:
            suffixes = app.parse_tld_list(",".join(tlds))
        except argparse.ArgumentTypeError as exc:
            raise ToolError(str(exc)) from None
        if limit < 1:
            raise ToolError("limit must be at least 1")
        per_tld = min(limit, max(1, MAX_PATTERN_CHECKS // len(suffixes)))
        candidates = RangeCandidatesUseCase(source, suffixes)
        ranker = RankCandidatesUseCase(CandidateOrder.INPUT, limit=per_tld, tlds=suffixes)
        domains = list(ranker.execute(candidates.execute()))
        if not domains:
            raise ToolError(f"no name fitting {pattern!r} is valid under these TLDs")
        dns = svc.dns if confirm_dns else None

        def work() -> dict[str, Any]:
            results, summary, unsupported = check_names(
                domains, run=run_opts, catalog=catalog, dns=dns
            )
            skipped_tlds = sorted({d.tld.suffix for d in unsupported})
            return {
                "pattern": pattern,
                "total": candidates.total(),
                "checked_per_tld": per_tld,
                "available": [r.domain.display for r in results if _is_free(r)],
                "results": [result_record(r) for r in results],
                "unsupported": [
                    {
                        "tld": suffix,
                        "reason": unsupported_message(suffix) or f"no registrar supports .{suffix}",
                    }
                    for suffix in skipped_tlds
                ],
                "summary": _summary_dict(summary),
            }

        return await anyio.to_thread.run_sync(work)

    @server.tool(
        name="watch_domains",
        description=(
            "Follow names over time. action 'add' / 'remove' edit the watchlist (full "
            "domain names); 'list' shows it with what was last seen; 'run' asks the "
            "registries afresh and returns only what changed since the last run: a name "
            "became available, started dropping (redemption or pending delete), was "
            "registered or restored, its expiry date moved or its parking changed. The "
            "first run of a name only records where it stands. The watchlist is the same "
            "file the CLI's 'domainhack watch' uses."
        ),
    )
    async def watch_domains(
        action: WatchAction, domains: list[str] | None = None
    ) -> dict[str, Any]:
        names = domains or []
        if action in ("add", "remove") and not names:
            raise ToolError(f"'{action}' needs at least one domain name")

        def work() -> dict[str, Any]:
            store = WatchStore(watchlist)
            try:
                if action == "add":
                    change = add_names(names, store, catalog)
                    return {
                        "added": change.done,
                        "already_watched": change.unchanged,
                        "refused": change.refused,
                        "watched": change.watched,
                    }
                if action == "remove":
                    change = remove_names(names, store)
                    return {
                        "removed": change.done,
                        "refused": change.refused,
                        "watched": change.watched,
                    }
                if action == "list":
                    return {"watched": [entry_json(e) for e in store.load().values()]}
                return run_watch(store, run_opts, catalog).to_dict()
            except WatchlistError as exc:
                raise ToolError(str(exc)) from None

        return await anyio.to_thread.run_sync(work)

    @server.tool(
        name="find_hacks_for_words",
        description=(
            "Which of these words end in a TLD, and the domain hack each one forms "
            "(plato -> pla.to, testing -> test.ing). Offline and instant: it does not check "
            "availability; pass the results to check_domains for that. 'checkable' says "
            "whether domainhack can check that TLD."
        ),
    )
    async def find_hacks_for_words(words: list[str], min_length: int = 0) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            checkable = set(svc.supported_tlds())
            hacks: list[dict[str, Any]] = []
            for raw in words:
                word = raw.strip().lower()
                if not word or (min_length and len(word) < min_length):
                    continue
                for suffix in svc.known_tlds.suffixes_of(word):
                    try:
                        hack = DomainHack.from_word(word, TLD(suffix))
                    except (ValueError, InvalidLabelError):
                        continue
                    if hack is None:
                        continue
                    top = suffix.rsplit(".", 1)[-1]
                    hacks.append(
                        {
                            "word": word,
                            "domain": hack.display,
                            "fqdn": hack.fqdn,
                            "tld": suffix,
                            "checkable": suffix in checkable or top in checkable,
                        }
                    )
            return {"hacks": hacks, "words": len(words)}

        return await anyio.to_thread.run_sync(work)

    @server.tool(
        name="list_tlds",
        description=(
            "Which TLDs domainhack can check, how (rdap or whois, and the registry host) "
            "or why not. Offline. By default: supported country-code TLDs; include_generic "
            "adds com/app/dev...; 'only' restricts to given suffixes."
        ),
    )
    async def list_tlds(
        kind: TldKind = "supported",
        include_generic: bool = False,
        only: list[str] | None = None,
    ) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            known = IanaTldList()
            if only:
                suffixes = [s.lower().lstrip(".") for s in only]
                unknown = [s for s in suffixes if not known.is_known(s)]
                if unknown:
                    raise ToolError(f"not a known TLD: {', '.join(unknown)}")
            else:
                suffixes = sorted(
                    t
                    for t in known.top_level_domains
                    if not t.startswith("xn--") and (include_generic or len(t) == 2)
                )
            backends = [describe_backend(s, rdap) for s in suffixes]
            if kind == "supported":
                backends = [b for b in backends if b.kind != "unsupported"]
            elif kind == "unsupported":
                backends = [b for b in backends if b.kind == "unsupported"]
            return {"tlds": [dataclasses.asdict(b) for b in backends], "count": len(backends)}

        return await anyio.to_thread.run_sync(work)

    return server


def main(argv: Sequence[str] | None = None) -> int:
    """Run the server over stdio until the client disconnects."""
    run = run_options(sys.argv[1:] if argv is None else argv)
    build_server(run=run).run("stdio")
    return 0
