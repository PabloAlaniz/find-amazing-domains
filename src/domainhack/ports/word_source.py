from abc import ABC, abstractmethod
from collections.abc import Iterator


class WordSource(ABC):
    """Port: provides an iterable of candidate words."""

    @abstractmethod
    def words(self) -> Iterator[str]: ...
