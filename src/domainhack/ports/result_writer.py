from abc import ABC, abstractmethod

from domainhack.domain.entities import DomainCheckResult


class ResultWriter(ABC):
    """Port: handles output of domain check results."""

    @abstractmethod
    def write_result(self, result: DomainCheckResult) -> None: ...

    def flush(self) -> None:  # noqa: B027
        """Finalize output. Default: no-op."""
