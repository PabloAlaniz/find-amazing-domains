import csv

from domainhack.adapters._registration import format_utc
from domainhack.adapters._text_sink import TextSink, TextTarget
from domainhack.domain.entities import DomainCheckResult
from domainhack.ports.result_writer import ResultWriter

# New columns are appended at the end so existing column positions stay stable.
CSV_FIELDS = (
    "fqdn",
    "display",
    "word",
    "sld",
    "tld",
    "availability",
    "error_message",
    "statuses",
    "expires_at",
    "registered_at",
    "registrar",
    "nameservers",
    "parked_hint",
    "dns_nameservers",
    "dns_conflict",
)
STATUS_SEPARATOR = ";"
LIST_SEPARATOR = STATUS_SEPARATOR


class CsvResultWriter(ResultWriter):
    """Streams domain check results to a CSV file, one row per result.

    ``fqdn`` is the ASCII name that was queried (A-label for IDNs); ``display``
    is the name as written (U-label), and equals ``fqdn`` for ASCII names.
    ``statuses`` is the registry status list joined with ``;`` and
    ``expires_at`` an ISO 8601 UTC date-time; both are empty when unknown.
    ``registered_at`` (ISO 8601 UTC), ``registrar``, ``nameservers`` (joined
    with ``;``) and ``parked_hint`` (the parking/aftermarket service the
    nameservers point to, e.g. ``domainrecover``) are registry details of a
    taken name, empty when unknown. ``dns_nameservers`` (joined with ``;``)
    are the NS hosts public DNS returned, empty when DNS was not consulted;
    ``dns_conflict`` is ``true`` when the registry said available but DNS
    shows a delegation, else ``false``.

    The header is written immediately and each row is flushed as it is written,
    so an interrupted run keeps every result checked so far.
    """

    def __init__(self, target: TextTarget) -> None:
        self._sink = TextSink(target)
        self._writer = csv.writer(self._sink.stream)
        self._writer.writerow(CSV_FIELDS)
        self._sink.stream.flush()

    def write_result(self, result: DomainCheckResult) -> None:
        domain = result.domain
        self._writer.writerow(
            (
                domain.fqdn,
                domain.display,
                domain.word,
                domain.sld,
                domain.tld.suffix,
                result.availability.value,
                result.error_message,
                STATUS_SEPARATOR.join(result.statuses),
                format_utc(result.expires_at),
                format_utc(result.registered_at),
                result.registrar,
                LIST_SEPARATOR.join(result.nameservers),
                result.parked_hint,
                LIST_SEPARATOR.join(result.dns.nameservers) if result.dns is not None else "",
                "true" if result.dns_conflict else "false",
            )
        )
        self._sink.stream.flush()

    def flush(self) -> None:
        self._sink.close()
