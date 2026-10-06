import sys

from domainhack.adapters._stderr import write_stderr
from domainhack.domain.entities import Availability, DomainCheckResult, DomainHack
from domainhack.ports.result_writer import ResultWriter


class ConsoleResultWriter(ResultWriter):
    """Writes domain check results to the console.

    Results (AVAILABLE, and TAKEN with ``show_taken``) go to stdout, so the
    output can be piped; ERROR lines are diagnostics and go to stderr. The run
    summary is printed by the CLI, from the use case's ``CheckSummary``.
    """

    def __init__(self, show_taken: bool = False, show_errors: bool = True) -> None:
        self._show_taken = show_taken
        self._show_errors = show_errors

    def write_result(self, result: DomainCheckResult) -> None:
        match result.availability:
            case Availability.AVAILABLE:
                print(f"  AVAILABLE: {_name(result.domain)} (word: {result.domain.word!r})")
            case Availability.TAKEN:
                if self._show_taken:
                    print(f"  TAKEN:     {_name(result.domain)}")
            case Availability.ERROR:
                if self._show_errors:
                    write_stderr(f"  ERROR:     {_name(result.domain)} -- {result.error_message}")

    def flush(self) -> None:
        sys.stdout.flush()


def _name(domain: DomainHack) -> str:
    """The name as written, plus the queried A-label for IDNs: ``ñandú.de (xn--and-6ma2c.de)``."""
    return f"{domain.display} ({domain.fqdn})" if domain.is_idn else domain.fqdn
