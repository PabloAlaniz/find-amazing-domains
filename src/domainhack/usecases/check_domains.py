from collections.abc import Iterable

from domainhack.domain.entities import DomainHack
from domainhack.ports.registrar import RegistrarClient
from domainhack.ports.result_writer import ResultWriter


class CheckDomainsUseCase:
    """Checks availability of domain hacks and writes results."""

    def __init__(self, registrar: RegistrarClient, writer: ResultWriter) -> None:
        self._registrar = registrar
        self._writer = writer

    def execute(self, domains: Iterable[DomainHack]) -> None:
        try:
            for domain in domains:
                result = self._registrar.check_availability(domain)
                self._writer.write_result(result)
        finally:
            self._writer.flush()
