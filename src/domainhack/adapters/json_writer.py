import json

from domainhack.adapters._registration import format_utc
from domainhack.adapters._text_sink import TextSink, TextTarget
from domainhack.domain.entities import DomainCheckResult
from domainhack.ports.result_writer import ResultWriter


class JsonResultWriter(ResultWriter):
    """Streams domain check results as JSON Lines (one JSON object per line).

    Each line is flushed as it is written, so an interrupted run leaves a valid
    file containing every result checked so far. ``fqdn`` is the queried ASCII
    name (A-label for IDNs) and ``display`` the name as written. ``statuses``
    is a list of registry statuses and ``expires_at`` an ISO 8601 UTC
    date-time or null. ``registered_at`` (ISO 8601 UTC or null),
    ``registrar``, ``nameservers`` (a list) and ``parked_hint`` (the
    parking/aftermarket service, e.g. ``"domainrecover"``) are registry
    details of a taken name, empty when unknown. ``dns_nameservers`` is the
    list of NS hosts public DNS returned (empty when DNS was not consulted)
    and ``dns_conflict`` is true when the registry said available but DNS
    shows a delegation.
    """

    def __init__(self, target: TextTarget) -> None:
        self._sink = TextSink(target)

    def write_result(self, result: DomainCheckResult) -> None:
        domain = result.domain
        record = {
            "fqdn": domain.fqdn,
            "display": domain.display,
            "word": domain.word,
            "sld": domain.sld,
            "tld": domain.tld.suffix,
            "availability": result.availability.value,
            "error_message": result.error_message,
            "statuses": list(result.statuses),
            "expires_at": format_utc(result.expires_at) or None,
            "registered_at": format_utc(result.registered_at) or None,
            "registrar": result.registrar,
            "nameservers": list(result.nameservers),
            "parked_hint": result.parked_hint,
            "dns_nameservers": list(result.dns.nameservers) if result.dns is not None else [],
            "dns_conflict": result.dns_conflict,
        }
        self._sink.stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._sink.stream.flush()

    def flush(self) -> None:
        self._sink.close()
