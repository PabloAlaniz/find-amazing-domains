from domainhack.domain.entities import Availability, DomainCheckResult, DomainHack
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
                print(f"  AVAILABLE: {_name(result.domain)} (word: {result.domain.word!r})")
            case Availability.TAKEN:
                if self._show_taken:
                    print(f"  TAKEN:     {_name(result.domain)}")
            case Availability.ERROR:
                if self._show_errors:
                    print(f"  ERROR:     {_name(result.domain)} -- {result.error_message}")

    def flush(self) -> None:
        print(f"\nDone. Checked {self._checked_count} domains, {self._available_count} available.")


def _name(domain: DomainHack) -> str:
    """The name as written, plus the queried A-label for IDNs: ``ñandú.de (xn--and-6ma2c.de)``."""
    return f"{domain.display} ({domain.fqdn})" if domain.is_idn else domain.fqdn
