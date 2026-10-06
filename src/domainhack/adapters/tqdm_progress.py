import sys
from typing import Any, TextIO

from tqdm import tqdm

from domainhack.domain.entities import Availability, DomainCheckResult
from domainhack.ports.progress import ProgressReporter


class _TqdmLineWriter:
    """File-like proxy installed as ``sys.stdout`` while a bar is visible.

    Complete lines are written inside ``tqdm.external_write_mode()``, which clears the
    bar, lets the line through to the real stream and redraws the bar, so ``print()``
    output on stdout doesn't get mangled by a bar on stderr.
    """

    def __init__(self, target: TextIO) -> None:
        self._target = target
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        if "\n" in self._buffer:
            complete, _, self._buffer = self._buffer.rpartition("\n")
            self._emit(complete + "\n")
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._emit(self._buffer)
            self._buffer = ""
        self._target.flush()

    def _emit(self, text: str) -> None:
        # external_write_mode() defaults to sys.stdout (this proxy), which tqdm treats
        # as sharing the terminal with a stderr bar, so the bar gets cleared.
        with tqdm.external_write_mode():
            self._target.write(text)
            self._target.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


class TqdmProgressReporter(ProgressReporter):
    """Shows a tqdm progress bar (on stderr by default) with available/error counts.

    ``disable=None`` lets tqdm turn the bar off automatically when ``file`` is not a TTY.
    While the bar is visible, ``sys.stdout`` is temporarily wrapped so regular prints
    are interleaved cleanly with the bar; it is restored on ``close()``.
    """

    def __init__(
        self,
        file: TextIO | None = None,
        disable: bool | None = None,
        desc: str = "Checking",
    ) -> None:
        self._file = file
        self._disable = disable
        self._desc = desc
        self._bar: tqdm[Any] | None = None
        self._saved_stdout: TextIO | None = None
        self.available = 0
        self.errors = 0

    def start(self, total: int | None) -> None:
        self.available = 0
        self.errors = 0
        self._bar = tqdm(
            total=total,
            file=self._file if self._file is not None else sys.stderr,
            disable=self._disable,
            desc=self._desc,
            unit="dom",
            dynamic_ncols=True,
        )
        if not self._bar.disable:
            self._bar.set_postfix(available=0, errors=0, refresh=False)
            self._saved_stdout = sys.stdout
            sys.stdout = _TqdmLineWriter(sys.stdout)

    def advance(self, result: DomainCheckResult) -> None:
        if result.availability is Availability.AVAILABLE:
            self.available += 1
        elif result.availability is Availability.ERROR:
            self.errors += 1
        if self._bar is None:
            return
        self._bar.set_postfix(available=self.available, errors=self.errors, refresh=False)
        self._bar.update(1)

    def close(self) -> None:
        if self._saved_stdout is not None:
            sys.stdout.flush()
            sys.stdout = self._saved_stdout
            self._saved_stdout = None
        if self._bar is not None:
            self._bar.close()
            self._bar = None
