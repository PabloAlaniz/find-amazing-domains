import sys

from domainhack.adapters._stderr import write_stderr
from domainhack.domain.entities import Availability, DomainCheckResult
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
                print(f"  AVAILABLE: {result.domain.fqdn} (word: {result.domain.word!r})")
            case Availability.TAKEN:
                if self._show_taken:
                    print(f"  TAKEN:     {result.domain.fqdn}")
            case Availability.ERROR:
                if self._show_errors:
                    write_stderr(f"  ERROR:     {result.domain.fqdn} -- {result.error_message}")

    def flush(self) -> None:
        sys.stdout.flush()
