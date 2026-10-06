"""Child process for the real-SIGINT test in ``test_parallel_check.py``.

Runs ``main`` with two hosts: .to answers at once, .io never answers. Once
the three .to results are written it prints ``waiting`` on stderr; the
parent then sends SIGINT and expects a clean exit 130 despite the hung
worker thread. Usage: ``python -m tests.parallel_sigint_child OUTPUT.csv``.
"""

from __future__ import annotations

import signal
import sys
import threading

from domainhack.cli import app
from domainhack.domain.entities import Availability, DomainCheckResult
from domainhack.ports.progress import ProgressReporter
from tests.fakes import ConcurrencyProbe, FakeCatalog, GatedRegistrar


class _AnnounceAfter(ProgressReporter):
    def __init__(self, after: int) -> None:
        self._after = after
        self._count = 0

    def start(self, total: int | None) -> None:
        pass

    def advance(self, result: DomainCheckResult) -> None:
        self._count += 1
        if self._count == self._after:
            print("waiting", file=sys.stderr, flush=True)

    def close(self) -> None:
        pass


def run(output: str) -> int:
    probe = ConcurrencyProbe()
    never = threading.Event()
    to = GatedRegistrar("to", probe, default=Availability.AVAILABLE)
    io = GatedRegistrar("io", probe, gates={"a.io": never})
    app._build_progress = lambda args: _AnnounceAfter(3)  # type: ignore[assignment]
    argv = [
        "--tld",
        "to,io",
        "check",
        "--range-max",
        "1",
        "--range-end",
        "c",
        "--no-cache",
        "--output",
        output,
    ]
    return app.main(argv, catalog=FakeCatalog(by_tld={"to": to, "io": io}))


if __name__ == "__main__":
    # A shell starts background jobs with SIGINT ignored, and children inherit
    # that; restore Python's default so the parent's SIGINT is a Ctrl-C.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    raise SystemExit(run(sys.argv[1]))
