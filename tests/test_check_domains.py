import pytest

from domainhack.domain.entities import TLD, Availability, DomainCheckResult, DomainHack
from domainhack.usecases.check_domains import CheckDomainsUseCase
from tests.conftest import CollectingWriter, FakeRegistrarClient


class TestCheckDomainsUseCase:
    def test_checks_all_domains(self) -> None:
        hack1 = DomainHack.from_word("plato", TLD("to"))
        hack2 = DomainHack.from_word("grato", TLD("to"))
        assert hack1 is not None and hack2 is not None

        results_map = {
            "pla.to": DomainCheckResult(domain=hack1, availability=Availability.AVAILABLE),
            "gra.to": DomainCheckResult(domain=hack2, availability=Availability.TAKEN),
        }
        registrar = FakeRegistrarClient(results_map)
        writer = CollectingWriter()

        uc = CheckDomainsUseCase(registrar, writer)
        uc.execute([hack1, hack2])

        assert len(writer.results) == 2
        assert writer.results[0].availability == Availability.AVAILABLE
        assert writer.results[1].availability == Availability.TAKEN

    def test_flush_called(self) -> None:
        writer = CollectingWriter()
        registrar = FakeRegistrarClient({})
        uc = CheckDomainsUseCase(registrar, writer)
        uc.execute([])
        assert writer.flushed is True

    def test_flush_called_on_error(self) -> None:
        class FailingRegistrar(FakeRegistrarClient):
            def check_availability(self, domain: DomainHack) -> DomainCheckResult:
                raise RuntimeError("network down")

        hack = DomainHack.from_word("plato", TLD("to"))
        assert hack is not None

        writer = CollectingWriter()
        registrar = FailingRegistrar({})
        uc = CheckDomainsUseCase(registrar, writer)

        with pytest.raises(RuntimeError):
            uc.execute([hack])

        assert writer.flushed is True
