from abc import ABC, abstractmethod

from domainhack.domain.entities import DomainCheckResult, DomainHack


class ResultCache(ABC):
    """Port: remembers check results between runs.

    ``CheckDomainsUseCase`` calls it from a single thread (the one running
    ``execute``), so implementations need not be thread-safe.
    """

    @abstractmethod
    def lookup(self, domain: DomainHack) -> DomainCheckResult | None:
        """A fresh cached result for ``domain``, or None to check it live."""

    @abstractmethod
    def store(self, result: DomainCheckResult) -> None:
        """Remember a live result (implementations decide which ones are worth keeping)."""
