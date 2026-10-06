from collections.abc import Sequence

from domainhack.domain.entities import DomainCheckResult
from domainhack.ports.result_writer import ResultWriter


class CompositeResultWriter(ResultWriter):
    """Fans out every result to several writers (e.g. console + file)."""

    def __init__(self, writers: Sequence[ResultWriter]) -> None:
        self._writers = tuple(writers)

    def write_result(self, result: DomainCheckResult) -> None:
        for writer in self._writers:
            writer.write_result(result)

    def flush(self) -> None:
        """Flush every writer, even if one fails; re-raise the first error."""
        first_error: Exception | None = None
        for writer in self._writers:
            try:
                writer.flush()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error
