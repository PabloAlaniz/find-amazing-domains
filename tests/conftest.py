from collections.abc import Iterator

from domainhack.domain.entities import DomainCheckResult, DomainHack
from domainhack.ports.registrar import RegistrarClient
from domainhack.ports.result_writer import ResultWriter
from domainhack.ports.word_source import WordSource


class FakeWordSource(WordSource):
    def __init__(self, words: list[str]) -> None:
        self._words = words

    def words(self) -> Iterator[str]:
        return iter(self._words)


class FakeRegistrarClient(RegistrarClient):
    def __init__(self, results: dict[str, DomainCheckResult]) -> None:
        self._results = results

    def check_availability(self, domain: DomainHack) -> DomainCheckResult:
        return self._results[domain.fqdn]


class CollectingWriter(ResultWriter):
    def __init__(self) -> None:
        self.results: list[DomainCheckResult] = []
        self.flushed = False

    def write_result(self, result: DomainCheckResult) -> None:
        self.results.append(result)

    def flush(self) -> None:
        self.flushed = True
