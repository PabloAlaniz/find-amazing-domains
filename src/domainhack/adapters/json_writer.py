import json

from domainhack.adapters._text_sink import TextSink, TextTarget
from domainhack.domain.entities import DomainCheckResult
from domainhack.ports.result_writer import ResultWriter


class JsonResultWriter(ResultWriter):
    """Streams domain check results as JSON Lines (one JSON object per line).

    Each line is flushed as it is written, so an interrupted run leaves a valid
    file containing every result checked so far. ``fqdn`` is the queried ASCII
    name (A-label for IDNs) and ``display`` the name as written.
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
        }
        self._sink.stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._sink.stream.flush()

    def flush(self) -> None:
        self._sink.close()
