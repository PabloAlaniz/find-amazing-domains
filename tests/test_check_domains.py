from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest

from domainhack.domain.entities import TLD, Availability, DomainCheckResult, DomainHack
from domainhack.usecases.check_domains import CheckDomainsUseCase, CheckSummary
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


def _hacks(*slds: str) -> list[DomainHack]:
    return [DomainHack.from_sld(sld, TLD("to")) for sld in slds]


def _registrar(outcomes: dict[str, Availability]) -> FakeRegistrarClient:
    return FakeRegistrarClient(
        {
            f"{sld}.to": DomainCheckResult(domain=_hacks(sld)[0], availability=availability)
            for sld, availability in outcomes.items()
        }
    )


class TestCheckSummary:
    def test_counts_per_availability(self) -> None:
        registrar = _registrar(
            {"a": Availability.AVAILABLE, "b": Availability.TAKEN, "c": Availability.ERROR}
        )
        summary = CheckDomainsUseCase(registrar, CollectingWriter()).execute(
            _hacks("a", "b", "c", "a")
        )
        assert summary == CheckSummary(available=2, taken=1, errors=1, interrupted=False)
        assert summary.checked == 4

    def test_empty_run(self) -> None:
        summary = CheckDomainsUseCase(FakeRegistrarClient({}), CollectingWriter()).execute([])
        assert summary == CheckSummary()
        assert summary.checked == 0

    def test_summary_is_frozen(self) -> None:
        summary = CheckSummary()
        with pytest.raises(AttributeError):
            summary.errors = 1  # type: ignore[misc]

    def test_keyboard_interrupt_from_registrar(self) -> None:
        class InterruptingRegistrar(FakeRegistrarClient):
            def check_availability(self, domain: DomainHack) -> DomainCheckResult:
                if domain.sld == "b":
                    raise KeyboardInterrupt
                return super().check_availability(domain)

        registrar = InterruptingRegistrar(
            {"a.to": DomainCheckResult(domain=_hacks("a")[0], availability=Availability.AVAILABLE)}
        )
        writer = CollectingWriter()
        progress = MagicMock()
        summary = CheckDomainsUseCase(registrar, writer, progress).execute(_hacks("a", "b", "c"))
        assert summary == CheckSummary(available=1, interrupted=True)
        assert [r.domain.sld for r in writer.results] == ["a"]
        assert writer.flushed
        progress.close.assert_called_once()

    def test_keyboard_interrupt_from_domain_source(self) -> None:
        def domains() -> Iterator[DomainHack]:
            yield from _hacks("a")
            raise KeyboardInterrupt

        writer = CollectingWriter()
        summary = CheckDomainsUseCase(_registrar({"a": Availability.TAKEN}), writer).execute(
            domains()
        )
        assert summary == CheckSummary(taken=1, interrupted=True)
        assert writer.flushed
