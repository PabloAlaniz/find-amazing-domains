from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class HandleStatus(Enum):
    TAKEN = "taken"
    NOT_FOUND = "not-found"  # nothing answers to that name: it looks free
    INVALID = "invalid"  # the service would never accept that name
    UNKNOWN = "unknown"  # the service could not be asked


@dataclass(frozen=True)
class HandleResult:
    """Whether ``handle`` is taken on ``service`` (``"github"``, ``"npm"``, ``"pypi"``).

    ``url`` is the page a person would open to see for themselves; ``detail``
    explains an ``INVALID`` or ``UNKNOWN`` status.
    """

    service: str
    handle: str
    status: HandleStatus
    url: str = ""
    detail: str = ""


class HandleChecker(ABC):
    """Port: is a name taken as a handle on the services a project lives on?"""

    @abstractmethod
    def check(self, name: str) -> list[HandleResult]:
        """One result per service, in a fixed order. Never raises for a service
        that fails: that is an ``UNKNOWN`` result."""
