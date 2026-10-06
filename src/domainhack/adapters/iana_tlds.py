"""The public suffixes a name can be registered under, from bundled package data.

Two files ship with the package:

- ``data/iana_tlds.txt``: a verbatim snapshot of IANA's
  https://data.iana.org/TLD/tlds-alpha-by-domain.txt (its first line, a
  ``# Version ...`` comment, says when it was taken). IDN TLDs appear in
  their ``xn--`` form and count as known.
- ``data/second_level.json``: a curated list of common registrable
  second-level suffixes (``com.ar``, ``co.uk``...), each checked against the
  ICANN section of the Public Suffix List (the file cites the version).

The list is snapshot-only on purpose: IANA adds TLDs a few times a year at
most, and a fixed list keeps hack suggestions deterministic and offline.
Refresh it by downloading the IANA file over ``data/iana_tlds.txt``
unchanged. Whether a suffix can actually be *checked* is a different
question, answered by ``registrar_catalog.supported_tlds``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from importlib import resources

from domainhack.ports.known_tlds import KnownTlds

IANA_SNAPSHOT = "data/iana_tlds.txt"
SECOND_LEVEL_FILE = "data/second_level.json"


def _read_data(name: str) -> str:
    return resources.files("domainhack").joinpath(name).read_text(encoding="utf-8")


def parse_iana_tlds(text: str) -> tuple[str, list[str]]:
    """Parse the IANA file into its ``# Version ...`` header (without ``#``) and lowercase TLDs."""
    version = ""
    tlds: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if not version:
                version = line.lstrip("#").strip()
            continue
        tlds.append(line.lower())
    return version, tlds


def load_iana_snapshot() -> tuple[str, list[str]]:
    """The bundled IANA list: (version header, lowercase TLDs)."""
    return parse_iana_tlds(_read_data(IANA_SNAPSHOT))


def load_second_level() -> list[str]:
    """The curated second-level suffixes, lowercase, in file order (LATAM first)."""
    data = json.loads(_read_data(SECOND_LEVEL_FILE))
    return [s.lower() for s in data["suffixes"]]


class IanaTldList(KnownTlds):
    """Known public suffixes: every IANA TLD plus curated second-level suffixes.

    ``tlds`` and ``second_level`` replace the bundled files (tests pass small
    lists). Matching is case-insensitive and ignores a leading dot.
    """

    def __init__(
        self,
        tlds: Iterable[str] | None = None,
        second_level: Iterable[str] | None = None,
    ) -> None:
        if tlds is None:
            self.version, tld_list = load_iana_snapshot()
        else:
            self.version, tld_list = "", list(tlds)
        levels = load_second_level() if second_level is None else list(second_level)
        self._tlds = frozenset(_normalize(t) for t in tld_list)
        self._second_level = tuple(dict.fromkeys(_normalize(s) for s in levels))
        self._all = self._tlds | frozenset(self._second_level)
        # "comar" -> ["com.ar"]: suffixes as they appear at the end of a word.
        self._by_joined: dict[str, list[str]] = {}
        for suffix in sorted(self._all):
            self._by_joined.setdefault(suffix.replace(".", ""), []).append(suffix)

    @property
    def top_level_domains(self) -> frozenset[str]:
        return self._tlds

    @property
    def second_level_suffixes(self) -> tuple[str, ...]:
        """The curated second-level suffixes, in file order."""
        return self._second_level

    def is_known(self, suffix: str) -> bool:
        return _normalize(suffix) in self._all

    def suffixes_of(self, name: str) -> list[str]:
        word = name.strip().lower()
        found: list[str] = []
        # Longest ending first; position 0 would leave an empty SLD.
        for start in range(1, len(word)):
            found.extend(self._by_joined.get(word[start:], ()))
        return found


def _normalize(suffix: str) -> str:
    return suffix.strip().lower().lstrip(".")
