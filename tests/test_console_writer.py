from domainhack.adapters.console_writer import ConsoleResultWriter
from domainhack.domain.entities import TLD, Availability, DomainCheckResult, DomainHack


def _hack(word: str = "plato") -> DomainHack:
    h = DomainHack.from_word(word, TLD("to"))
    assert h is not None
    return h


def _result(availability: Availability, word: str = "plato") -> DomainCheckResult:
    return DomainCheckResult(
        domain=_hack(word),
        availability=availability,
        error_message="fail" if availability == Availability.ERROR else "",
    )


class TestConsoleResultWriter:
    def test_prints_available(self, capsys) -> None:
        writer = ConsoleResultWriter()
        writer.write_result(_result(Availability.AVAILABLE))
        out = capsys.readouterr().out
        assert "AVAILABLE" in out
        assert "pla.to" in out

    def test_hides_taken_by_default(self, capsys) -> None:
        writer = ConsoleResultWriter()
        writer.write_result(_result(Availability.TAKEN))
        out = capsys.readouterr().out
        assert out == ""

    def test_shows_taken_when_enabled(self, capsys) -> None:
        writer = ConsoleResultWriter(show_taken=True)
        writer.write_result(_result(Availability.TAKEN))
        out = capsys.readouterr().out
        assert "TAKEN" in out
        assert "pla.to" in out

    def test_shows_errors_by_default(self, capsys) -> None:
        writer = ConsoleResultWriter()
        writer.write_result(_result(Availability.ERROR))
        out = capsys.readouterr().out
        assert "ERROR" in out
        assert "fail" in out

    def test_hides_errors_when_disabled(self, capsys) -> None:
        writer = ConsoleResultWriter(show_errors=False)
        writer.write_result(_result(Availability.ERROR))
        out = capsys.readouterr().out
        assert out == ""

    def test_flush_prints_summary(self, capsys) -> None:
        writer = ConsoleResultWriter()
        writer.write_result(_result(Availability.AVAILABLE))
        writer.write_result(_result(Availability.TAKEN, "grato"))
        capsys.readouterr()  # clear
        writer.flush()
        out = capsys.readouterr().out
        assert "Checked 2" in out
        assert "1 available" in out

    def test_counts(self) -> None:
        writer = ConsoleResultWriter()
        writer.write_result(_result(Availability.AVAILABLE))
        writer.write_result(_result(Availability.AVAILABLE, "grato"))
        writer.write_result(_result(Availability.TAKEN, "abeto"))
        assert writer._available_count == 2
        assert writer._checked_count == 3
