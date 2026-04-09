from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from domainhack.domain.entities import DomainCheckResult, DomainHack


class RegistrarClient(ABC):
    """Port: checks domain availability against a registrar."""

    @abstractmethod
    def check_availability(self, domain: DomainHack) -> DomainCheckResult: ...

    def close(self) -> None:  # noqa: B027
        """Release resources. Override if needed."""

    def __enter__(self) -> RegistrarClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        self.close()
