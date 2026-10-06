from collections.abc import Iterable

from domainhack.domain.entities import DomainHack
from domainhack.ports.progress import NullProgressReporter, ProgressReporter
from domainhack.ports.registrar import RegistrarClient
from domainhack.ports.result_writer import ResultWriter


class CheckDomainsUseCase:
    """Checks availability of domain hacks and writes results."""

    def __init__(
        self,
        registrar: RegistrarClient,
        writer: ResultWriter,
        progress: ProgressReporter | None = None,
    ) -> None:
        self._registrar = registrar
        self._writer = writer
        self._progress = progress if progress is not None else NullProgressReporter()

    def execute(self, domains: Iterable[DomainHack], total: int | None = None) -> None:
        try:
            self._progress.start(total)
            for domain in domains:
                result = self._registrar.check_availability(domain)
                self._writer.write_result(result)
                self._progress.advance(result)
        finally:
            # Close the bar first so the writer's summary is printed below it.
            try:
                self._progress.close()
            finally:
                self._writer.flush()
