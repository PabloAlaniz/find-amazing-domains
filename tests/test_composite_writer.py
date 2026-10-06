import pytest

from domainhack.adapters.composite_writer import CompositeResultWriter
from domainhack.domain.entities import TLD, Availability, DomainCheckResult, DomainHack
from domainhack.ports.result_writer import ResultWriter
from tests.fakes import CollectingWriter


def _result() -> DomainCheckResult:
    hack = DomainHack.from_word("plato", TLD("to"))
    assert hack is not None
    return DomainCheckResult(domain=hack, availability=Availability.AVAILABLE)


class FailingFlushWriter(CollectingWriter):
    def flush(self) -> None:
        super().flush()
        raise OSError("disk full")


class TestCompositeResultWriter:
    def test_fans_out_results(self) -> None:
        a, b = CollectingWriter(), CollectingWriter()
        composite = CompositeResultWriter([a, b])
        result = _result()
        composite.write_result(result)
        assert a.results == [result]
        assert b.results == [result]

    def test_flushes_all_writers(self) -> None:
        a, b = CollectingWriter(), CollectingWriter()
        CompositeResultWriter([a, b]).flush()
        assert a.flushed
        assert b.flushed

    def test_flush_continues_after_failure_and_reraises(self) -> None:
        failing, ok = FailingFlushWriter(), CollectingWriter()
        composite = CompositeResultWriter([failing, ok])
        with pytest.raises(OSError, match="disk full"):
            composite.flush()
        assert failing.flushed
        assert ok.flushed

    def test_empty_composite_is_noop(self) -> None:
        writers: list[ResultWriter] = []
        composite = CompositeResultWriter(writers)
        composite.write_result(_result())
        composite.flush()
