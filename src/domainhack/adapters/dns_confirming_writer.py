"""A ResultWriter decorator that adds DNS evidence before results are written."""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor

from domainhack.domain.entities import DomainCheckResult
from domainhack.ports.result_writer import ResultWriter
from domainhack.usecases.confirm_dns import ConfirmWithDns


class DnsConfirmingWriter(ResultWriter):
    """Confirms each result with DNS (``ConfirmWithDns``), then passes it on.

    Results stream: each lookup starts as soon as its result arrives, on a
    pool of ``confirm.workers`` threads, and results are handed to ``inner``
    in arrival order as soon as every earlier one is done. ``inner`` is only
    called from the thread that calls ``write_result``/``flush`` (the
    check's main thread), never from the pool.

    ``flush`` waits for the outstanding lookups. If that wait is interrupted
    (Ctrl-C), the results still pending are written without DNS evidence
    and ``inner`` is flushed anyway, so partial output files are kept.
    ``conflicts`` counts results written with ``dns_conflict``.
    """

    def __init__(self, inner: ResultWriter, confirm: ConfirmWithDns) -> None:
        self._inner = inner
        self._confirm = confirm
        self._pool = ThreadPoolExecutor(
            max_workers=confirm.workers, thread_name_prefix="dns-confirm"
        )
        self._pending: deque[tuple[DomainCheckResult, Future[DomainCheckResult] | None]] = deque()
        self.conflicts = 0

    def write_result(self, result: DomainCheckResult) -> None:
        future = (
            self._pool.submit(self._confirm.confirm, result)
            if self._confirm.applies_to(result)
            else None
        )
        self._pending.append((result, future))
        self._emit_ready()

    def flush(self) -> None:
        try:
            while self._pending:
                original, future = self._pending[0]
                confirmed = future.result() if future is not None else original
                self._pending.popleft()
                self._emit(confirmed)
        finally:
            # Interrupted: keep what was checked, without the missing evidence.
            while self._pending:
                original, future = self._pending.popleft()
                done = future is not None and future.done() and future.exception() is None
                self._emit(future.result() if done and future is not None else original)
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._inner.flush()

    def _emit_ready(self) -> None:
        while self._pending:
            original, future = self._pending[0]
            if future is not None and not future.done():
                return
            self._pending.popleft()
            self._emit(future.result() if future is not None else original)

    def _emit(self, result: DomainCheckResult) -> None:
        self.conflicts += result.dns_conflict
        self._inner.write_result(result)
