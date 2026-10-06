import io
import sys

import pytest

from domainhack.adapters.tqdm_progress import TqdmProgressReporter
from domainhack.domain.entities import TLD, Availability, DomainCheckResult, DomainHack
from domainhack.ports.progress import NullProgressReporter, ProgressReporter
from domainhack.usecases.check_domains import CheckDomainsUseCase
from tests.conftest import CollectingWriter, FakeRegistrarClient


def _result(sld: str, availability: Availability) -> DomainCheckResult:
    return DomainCheckResult(domain=DomainHack.from_sld(sld, TLD("to")), availability=availability)


class RecordingProgress(ProgressReporter):
    def __init__(self) -> None:
        self.events: list[str] = []
        self.total: int | None = -1
        self.advanced: list[DomainCheckResult] = []

    def start(self, total: int | None) -> None:
        self.events.append("start")
        self.total = total

    def advance(self, result: DomainCheckResult) -> None:
        self.events.append("advance")
        self.advanced.append(result)

    def close(self) -> None:
        self.events.append("close")


class TestProgressPort:
    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            ProgressReporter()  # type: ignore[abstract]

    def test_null_reporter_is_noop(self) -> None:
        reporter = NullProgressReporter()
        reporter.start(10)
        reporter.advance(_result("pla", Availability.AVAILABLE))
        reporter.close()

    def test_use_case_defaults_to_null_reporter(self) -> None:
        uc = CheckDomainsUseCase(FakeRegistrarClient({}), CollectingWriter())
        assert isinstance(uc._progress, NullProgressReporter)


class TestUseCaseReportsProgress:
    def test_start_advance_close(self) -> None:
        r1 = _result("pla", Availability.AVAILABLE)
        r2 = _result("gra", Availability.TAKEN)
        registrar = FakeRegistrarClient({"pla.to": r1, "gra.to": r2})
        progress = RecordingProgress()

        CheckDomainsUseCase(registrar, CollectingWriter(), progress).execute(
            [r1.domain, r2.domain], total=2
        )

        assert progress.events == ["start", "advance", "advance", "close"]
        assert progress.total == 2
        assert progress.advanced == [r1, r2]

    def test_total_defaults_to_none(self) -> None:
        progress = RecordingProgress()
        CheckDomainsUseCase(FakeRegistrarClient({}), CollectingWriter(), progress).execute([])
        assert progress.total is None

    def test_close_and_flush_on_error(self) -> None:
        class FailingRegistrar(FakeRegistrarClient):
            def check_availability(self, domain: DomainHack) -> DomainCheckResult:
                raise RuntimeError("network down")

        progress = RecordingProgress()
        writer = CollectingWriter()
        uc = CheckDomainsUseCase(FailingRegistrar({}), writer, progress)

        with pytest.raises(RuntimeError):
            uc.execute([DomainHack.from_sld("pla", TLD("to"))])

        assert progress.events == ["start", "close"]
        assert writer.flushed is True

    def test_flush_even_if_close_fails(self) -> None:
        class BrokenProgress(RecordingProgress):
            def close(self) -> None:
                raise RuntimeError("boom")

        writer = CollectingWriter()
        uc = CheckDomainsUseCase(FakeRegistrarClient({}), writer, BrokenProgress())
        with pytest.raises(RuntimeError, match="boom"):
            uc.execute([])
        assert writer.flushed is True


class TestTqdmProgressReporter:
    def test_renders_counts_and_total(self) -> None:
        buf = io.StringIO()
        reporter = TqdmProgressReporter(file=buf, disable=False)
        reporter.start(3)
        reporter.advance(_result("pla", Availability.AVAILABLE))
        reporter.advance(_result("gra", Availability.ERROR))
        reporter.advance(_result("pro", Availability.TAKEN))
        reporter.close()

        output = buf.getvalue()
        assert "3/3" in output
        assert "available=1" in output
        assert "errors=1" in output
        assert reporter.available == 1
        assert reporter.errors == 1

    def test_indeterminate_total(self) -> None:
        buf = io.StringIO()
        reporter = TqdmProgressReporter(file=buf, disable=False)
        reporter.start(None)
        reporter.advance(_result("pla", Availability.TAKEN))
        reporter.close()
        assert "1dom" in buf.getvalue()

    def test_auto_disabled_when_not_a_tty(self) -> None:
        buf = io.StringIO()
        original_stdout = sys.stdout
        reporter = TqdmProgressReporter(file=buf)  # disable=None -> off for non-TTY
        reporter.start(1)
        assert sys.stdout is original_stdout
        reporter.advance(_result("pla", Availability.AVAILABLE))
        reporter.close()
        assert buf.getvalue() == ""
        assert reporter.available == 1

    def test_stdout_prints_routed_and_restored(self, capsys: pytest.CaptureFixture[str]) -> None:
        buf = io.StringIO()
        original_stdout = sys.stdout
        reporter = TqdmProgressReporter(file=buf, disable=False)
        reporter.start(1)
        assert sys.stdout is not original_stdout
        print("  AVAILABLE: pla.to")
        print("partial", end="")
        reporter.advance(_result("pla", Availability.AVAILABLE))
        reporter.close()

        assert sys.stdout is original_stdout
        out = capsys.readouterr().out
        assert "  AVAILABLE: pla.to\n" in out
        assert out.endswith("partial")
        assert "AVAILABLE" not in buf.getvalue()

    def test_close_without_start_is_safe(self) -> None:
        TqdmProgressReporter(file=io.StringIO(), disable=False).close()
