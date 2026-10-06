import csv

from domainhack.adapters._text_sink import TextSink, TextTarget
from domainhack.domain.entities import DomainCheckResult
from domainhack.ports.result_writer import ResultWriter

CSV_FIELDS = ("fqdn", "display", "word", "sld", "tld", "availability", "error_message")


class CsvResultWriter(ResultWriter):
    """Streams domain check results to a CSV file, one row per result.

    ``fqdn`` is the ASCII name that was queried (A-label for IDNs); ``display``
    is the name as written (U-label), and equals ``fqdn`` for ASCII names.

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
            )
        )
        self._sink.stream.flush()

    def flush(self) -> None:
        self._sink.close()
