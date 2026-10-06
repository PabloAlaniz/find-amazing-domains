from abc import ABC, abstractmethod

from domainhack.domain.entities import DomainCheckResult


class ProgressReporter(ABC):
    """Port: reports progress while domains are being checked."""

    @abstractmethod
    def start(self, total: int | None) -> None:
        """Begin reporting. ``total`` is the expected number of checks, or None if unknown."""

    @abstractmethod
    def advance(self, result: DomainCheckResult) -> None:
        """Record that one more domain has been checked."""

    @abstractmethod
    def close(self) -> None:
        """Finish reporting and release any resources."""


class NullProgressReporter(ProgressReporter):
    """Null object: reports nothing. Default when no progress output is wanted."""

    def start(self, total: int | None) -> None:
        pass

    def advance(self, result: DomainCheckResult) -> None:
        pass

    def close(self) -> None:
        pass
