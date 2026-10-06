from abc import ABC, abstractmethod

from domainhack.domain.entities import DnsEvidence


class DnsLookup(ABC):
    """Port: public DNS evidence for a fully qualified (ASCII) domain name."""

    @abstractmethod
    def lookup(self, fqdn: str) -> DnsEvidence:
        """Never raises for DNS failures: they are reported in ``DnsEvidence.error``."""
