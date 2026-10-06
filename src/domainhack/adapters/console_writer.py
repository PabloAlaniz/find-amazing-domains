import sys

from domainhack.adapters._stderr import write_stderr
from domainhack.domain.entities import Availability, DomainCheckResult, DomainHack
from domainhack.ports.result_writer import ResultWriter


class ConsoleResultWriter(ResultWriter):
    """Writes domain check results to the console.

    Results (AVAILABLE, and TAKEN with ``show_taken``) go to stdout, so the
    output can be piped; ERROR lines are diagnostics and go to stderr. TAKEN
    names in redemption or pending delete ("dropping") are printed with their
    status and expiry date, and ``show_dropping`` prints them even without
    ``show_taken``. The run summary is printed by the CLI, from the use case's
    ``CheckSummary``.
    """

    def __init__(
        self, show_taken: bool = False, show_errors: bool = True, show_dropping: bool = False
    ) -> None:
        self._show_taken = show_taken
        self._show_errors = show_errors
        self._show_dropping = show_dropping

    def write_result(self, result: DomainCheckResult) -> None:
        match result.availability:
            case Availability.AVAILABLE:
                print(f"  AVAILABLE: {_name(result.domain)} (word: {result.domain.word!r})")
            case Availability.TAKEN:
                if result.is_dropping:
                    if self._show_taken or self._show_dropping:
                        print(f"  TAKEN ({_dropping_detail(result)}): {_name(result.domain)}")
                elif self._show_taken:
                    print(f"  TAKEN:     {_name(result.domain)}")
            case Availability.ERROR:
                if self._show_errors:
                    write_stderr(f"  ERROR:     {_name(result.domain)} -- {result.error_message}")

    def flush(self) -> None:
        sys.stdout.flush()


def _dropping_detail(result: DomainCheckResult) -> str:
    """``dropping: pending delete, expires 2026-11-02`` (the date only when known)."""
    detail = f"dropping: {', '.join(result.dropping_statuses)}"
    if result.expires_at is not None:
        detail += f", expires {result.expires_at.date().isoformat()}"
    return detail


def _name(domain: DomainHack) -> str:
    """The name as written, plus the queried A-label for IDNs: ``ñandú.de (xn--and-6ma2c.de)``."""
    return f"{domain.display} ({domain.fqdn})" if domain.is_idn else domain.fqdn
