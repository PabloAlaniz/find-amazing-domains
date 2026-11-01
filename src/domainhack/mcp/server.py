"""domainhack as an MCP server, so an agent can find a domain while it names a project.

Four tools, built on the same use cases as the CLI:

- ``find_domain_for_name``: what ``domainhack name`` does, as structured data;
- ``check_domains``: availability of specific names, with registration details;
- ``find_hacks_for_words``: which words end in a TLD (offline, no network);
- ``list_tlds``: which TLDs can be checked, how, and why not (offline).

Run ``domainhack-mcp`` (stdio transport). It takes the run options the CLI
shares (``--delay``, ``--parallel``, ``--no-cache``, ``--cache-path``,
``--contact``); the progress bar is always off because stdout carries the
protocol. Install with ``pip install "domainhack[mcp]"``.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Sequence
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
from domainhack.adapters.cached_registrar import CachedRegistrarClient
from domainhack.adapters.iana_tlds import IanaTldList
from domainhack.adapters.json_writer import result_record
from domainhack.adapters.rdap_bootstrap import RdapBootstrap
from domainhack.adapters.registrar_catalog import describe_backend
from domainhack.adapters.registry_sources import unsupported_message
from domainhack.cli import app
from domainhack.cli.name_cmd import (
    NameServices,
    NameUsageError,
    ResultCollector,
    default_services,
    find_name,
    validate_brand_name,
)
from domainhack.cli.tlds_cmd import offline_bootstrap
from domainhack.domain.entities import TLD, DomainCheckResult, DomainHack, InvalidLabelError
from domainhack.ports.dns_lookup import DnsLookup
from domainhack.usecases.check_domains import CheckDomainsUseCase, CheckSummary
from domainhack.usecases.confirm_dns import ConfirmWithDns

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
- find_hacks_for_words and list_tlds are offline and instant.

Registries limit automated lookups: a call may take several seconds per TLD, results are
cached, and "available" means "not registered" (premium or reserved names may still show as
available). Confirm with a registrar before buying.
"""

TldKind = Literal["supported", "unsupported", "all"]


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
    args = parser.parse_args(list(argv))
    args.no_progress = True
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


def parse_domains(values: Sequence[str]) -> tuple[list[DomainHack], list[dict[str, str]]]:
    """Full domain names (``pla.to``, ``foto.com.ar``) as ``DomainHack`` values.

    Returns the valid ones (deduplicated by fqdn, request order) and, for each
    invalid one, ``{"domain": ..., "reason": ...}``.
    """
    domains: list[DomainHack] = []
    rejected: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in values:
        text = raw.strip().lower().rstrip(".")
        sld, dot, suffix = text.partition(".")
        if not dot or not sld or not suffix:
            rejected.append({"domain": raw, "reason": "expected a full domain name like pla.to"})
            continue
        try:
            domain = DomainHack.from_sld(sld, TLD(suffix))
        except (ValueError, InvalidLabelError) as exc:
            rejected.append({"domain": raw, "reason": str(exc)})
            continue
        if domain.fqdn not in seen:
            seen.add(domain.fqdn)
            domains.append(domain)
    return domains, rejected


def _check(
    domains: Sequence[DomainHack],
    *,
    run: argparse.Namespace,
    catalog: app.RegistrarCatalog | None,
    dns: DnsLookup | None,
) -> tuple[list[DomainCheckResult], CheckSummary | None, list[DomainHack]]:
    """Check ``domains`` like ``domainhack check``; unsupported TLDs are reported, not raised."""
    router = app._build_router(run, catalog)
    try:
        unsupported = [d for d in domains if not router.supports(d.tld)]
        supported = [d for d in domains if router.supports(d.tld)]
        if not supported:
            return [], None, unsupported
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
        summary = use_case.execute(supported, total=len(supported))
    order = {d.fqdn: i for i, d in enumerate(supported)}
    results = sorted(collector.results, key=lambda r: order[r.domain.fqdn])
    if dns is not None and not summary.interrupted:
        results = ConfirmWithDns(dns).apply(results)
    return results, summary, unsupported


def _unsupported_entry(domain: DomainHack) -> dict[str, str]:
    reason = unsupported_message(domain.tld.suffix) or f"no registrar supports .{domain.tld.suffix}"
    return {"domain": domain.display, "reason": reason}


def build_server(
    *,
    run: argparse.Namespace | None = None,
    catalog: app.RegistrarCatalog | None = None,
    services: NameServices | None = None,
    bootstrap: RdapBootstrap | None = None,
) -> MCPServer:
    """The MCP server with its four tools.

    Requests that cannot be served (invalid name, no valid TLD, no registrar
    for any candidate) raise ``ToolError``, which the client sees as a tool
    error with that message. ``run`` holds the shared run options (default:
    the CLI defaults, no progress bar). ``catalog`` and ``services`` replace the registrar catalog
    and the ``name`` services, as in the CLI (tests pass fakes). ``bootstrap``
    is the RDAP bootstrap ``list_tlds`` describes (default: the bundled
    snapshot, offline).
    """
    run_opts = run if run is not None else run_options()
    svc = services if services is not None else default_services()
    rdap = bootstrap if bootstrap is not None else offline_bootstrap()
    presets = ", ".join(load_tld_presets())

    server = MCPServer(SERVER_NAME, version=__version__, instructions=INSTRUCTIONS)

    @server.tool(
        name="find_domain_for_name",
        description=(
            "Find a domain for a project or brand name: checks the exact name under a set of "
            f"TLDs (presets: {presets}; or suffixes, mixed: 'startup,com.ar,la'), finds domain "
            "hacks that spell it, optionally brand variants, and returns a ranked "
            "recommendation with registration details for taken names. Queries the "
            "registries live; may take several seconds."
        ),
    )
    async def find_domain_for_name(
        name: str,
        tlds: str = DEFAULT_PRESET,
        variants: bool = False,
        confirm_dns: bool = True,
    ) -> dict[str, Any]:
        try:
            clean = validate_brand_name(name)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        warnings: list[str] = []

        def work() -> dict[str, Any]:
            try:
                outcome = find_name(
                    clean,
                    tlds,
                    run=run_opts,
                    variants=variants,
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
            results, summary, unsupported = _check(parsed, run=run_opts, catalog=catalog, dns=dns)
            return {
                "results": [result_record(r) for r in results],
                "unsupported": [_unsupported_entry(d) for d in unsupported],
                "rejected": rejected,
                "summary": _summary_dict(summary),
            }

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
