from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from domainhack.domain.entities import Availability, DomainHack
from domainhack.ports.progress import NullProgressReporter, ProgressReporter
from domainhack.ports.registrar import RegistrarClient
from domainhack.ports.result_writer import ResultWriter


@dataclass(frozen=True)
class CheckSummary:
    """Outcome of a run: how many domains were checked and how they came out."""

    available: int = 0
    taken: int = 0
    errors: int = 0
    interrupted: bool = False

    @property
    def checked(self) -> int:
        return self.available + self.taken + self.errors


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

    def execute(self, domains: Iterable[DomainHack], total: int | None = None) -> CheckSummary:
        """Check every domain and return a summary.

        A ``KeyboardInterrupt`` stops the run cleanly: the writers are flushed
        (so partial output files are kept) and the summary has
        ``interrupted=True``. Any other exception propagates after the flush.
        """
        counts = dict.fromkeys(Availability, 0)
        interrupted = False
        try:
            self._progress.start(total)
            try:
                for domain in domains:
                    result = self._registrar.check_availability(domain)
                    self._writer.write_result(result)
                    counts[result.availability] += 1
                    self._progress.advance(result)
            except KeyboardInterrupt:
                interrupted = True
        finally:
            # Close the bar first so anything printed afterwards appears below it.
            try:
                self._progress.close()
            finally:
                self._writer.flush()
        return CheckSummary(
            available=counts[Availability.AVAILABLE],
            taken=counts[Availability.TAKEN],
            errors=counts[Availability.ERROR],
            interrupted=interrupted,
        )
