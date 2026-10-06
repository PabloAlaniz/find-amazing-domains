from abc import ABC, abstractmethod


class KnownTlds(ABC):
    """Port: the set of public suffixes a name can be registered under.

    Suffixes are lowercase, without a leading dot, and may have several
    labels (``"to"``, ``"com.ar"``, ``"co.uk"``).
    """

    @abstractmethod
    def is_known(self, suffix: str) -> bool: ...

    @abstractmethod
    def suffixes_of(self, name: str) -> list[str]:
        """Known suffixes that ``name`` ends with and that leave a non-empty SLD.

        Only plain concatenation is considered: ``"plato"`` -> ``["to"]``;
        dots in multi-label suffixes are ignored, so ``"fotocomar"`` yields
        ``"com.ar"`` (``foto.com.ar``). Longest suffix first.
        """
