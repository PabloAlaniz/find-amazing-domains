from domainhack.domain.entities import Availability, DomainCheckResult
from domainhack.ports.result_writer import ResultWriter


class ConsoleResultWriter(ResultWriter):
    """Writes domain check results to the console."""

    def __init__(self, show_taken: bool = False, show_errors: bool = True) -> None:
        self._show_taken = show_taken
        self._show_errors = show_errors
        self._available_count = 0
        self._checked_count = 0

    def write_result(self, result: DomainCheckResult) -> None:
        self._checked_count += 1
        match result.availability:
            case Availability.AVAILABLE:
                self._available_count += 1
                print(f"  AVAILABLE: {result.domain.fqdn} (word: {result.domain.word!r})")
            case Availability.TAKEN:
                if self._show_taken:
                    print(f"  TAKEN:     {result.domain.fqdn}")
            case Availability.ERROR:
                if self._show_errors:
                    print(f"  ERROR:     {result.domain.fqdn} -- {result.error_message}")

    def flush(self) -> None:
        print(f"\nDone. Checked {self._checked_count} domains, {self._available_count} available.")
