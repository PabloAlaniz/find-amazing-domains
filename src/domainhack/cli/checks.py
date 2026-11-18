"""Check a given list of full domain names, for the commands that are handed
names instead of generating them (``watch``, and the MCP tool ``check_domains``)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from domainhack.adapters.cached_registrar import CachedRegistrarClient
from domainhack.cli import app
from domainhack.cli.name_cmd import ResultCollector
from domainhack.domain.entities import TLD, DomainCheckResult, DomainHack, InvalidLabelError
from domainhack.ports.dns_lookup import DnsLookup
from domainhack.usecases.check_domains import CheckDomainsUseCase, CheckSummary
from domainhack.usecases.confirm_dns import ConfirmWithDns


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


def check_names(
    domains: Sequence[DomainHack],
    *,
    run: argparse.Namespace,
    catalog: app.RegistrarCatalog | None,
    dns: DnsLookup | None,
) -> tuple[list[DomainCheckResult], CheckSummary | None, list[DomainHack]]:
    """Check ``domains`` like ``domainhack check``; unsupported TLDs are reported, not raised.

    Returns the results in request order, the run summary (None when nothing
    could be checked) and the domains whose TLD no registrar supports.
    """
    router = app._build_router(run, catalog)
    try:
        unsupported = [d for d in domains if not router.supports(d.tld)]
        supported = [d for d in domains if router.supports(d.tld)]
        if not supported:
            router.close()
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
