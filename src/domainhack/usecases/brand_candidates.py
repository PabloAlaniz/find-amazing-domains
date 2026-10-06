"""Candidate domains for a brand name: the exact name, brand variants and domain hacks.

``domainhack name sumanda`` checks three kinds of names:

- EXACT: ``sumanda.<tld>`` for every requested TLD.
- VARIANT (opt-in): ``get``/``use``/``try``/``hola``/``my`` + name and
  name + ``hq``/``app``/``labs``/``studio``, only under ``.com`` plus the
  first ``VARIANT_TLD_LIMIT`` other requested TLDs. That bounds variants to
  9 x 4 = 36 names whatever the preset (``all-supported`` included).
- HACK: (a) the name split at a known TLD it ends with (``plato`` ->
  ``pla.to``); (b) the name plus a curated word whose ending is a known TLD
  (``sumanda`` + ``studio`` -> ``sumandastud.io``). Hacks may use TLDs outside
  the requested ones: finding them is the point.

Every candidate goes through ``DomainHack.from_sld`` (label rules, IDN);
invalid ones are counted in ``skipped``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from domainhack.domain.entities import TLD, DomainHack, InvalidLabelError
from domainhack.ports.known_tlds import KnownTlds

VARIANT_PREFIXES: tuple[str, ...] = ("get", "use", "try", "hola", "my")
VARIANT_SUFFIXES: tuple[str, ...] = ("hq", "app", "labs", "studio")
# Variants are checked under .com plus this many other requested TLDs (in
# request order, i.e. the preset's top ones).
VARIANT_TLD_LIMIT = 3
VARIANT_BASE_TLD = "com"

# A preset whose value is this string expands to every TLD the catalog supports.
CATALOG_PRESET = "catalog"

Presets = Mapping[str, Sequence[str] | str]


class CandidateKind(Enum):
    EXACT = "exact"
    VARIANT = "variant"
    HACK = "hack"


@dataclass(frozen=True)
class HackWord:
    """A word appended to the name to form a hack, with its short meaning."""

    word: str
    meaning: str


@dataclass(frozen=True)
class BrandCandidate:
    """One name to check, why it is a candidate, and how a person would read it.

    ``label`` is the human reading (``"sumanda studio"`` for ``sumandastud.io``);
    ``meaning`` explains a hack word (empty otherwise).
    """

    domain: DomainHack
    kind: CandidateKind
    label: str
    meaning: str = ""


@dataclass(frozen=True)
class TldSelection:
    """``--tlds`` resolved: valid TLDs in request order, plus rejected suffixes."""

    tlds: tuple[TLD, ...]
    rejected: tuple[str, ...] = ()


def resolve_tld_spec(
    spec: str, presets: Presets, all_supported: Callable[[], Iterable[str]]
) -> TldSelection:
    """Expand ``"startup,com.ar,la"``: preset names and raw suffixes, mixed.

    Order is preserved and duplicates dropped. Suffixes ``TLD`` rejects are
    returned in ``rejected`` (the caller warns) instead of failing the run,
    so presets may list suffixes this version cannot check yet; the catalog
    preset drops them silently.
    """
    tlds: list[TLD] = []
    rejected: list[str] = []
    for raw in spec.split(","):
        token = raw.strip().lstrip(".").lower()
        if not token:
            continue
        preset = presets.get(token)
        if preset is None:
            suffixes: Iterable[str] = (token,)
        elif preset == CATALOG_PRESET:
            suffixes = sorted(all_supported())
        else:
            suffixes = preset
        for suffix in suffixes:
            try:
                tld = TLD(suffix)
            except ValueError:
                # The catalog lists IDN TLDs (xn--...) nobody asked for by name.
                if preset != CATALOG_PRESET and suffix not in rejected:
                    rejected.append(suffix)
                continue
            if tld not in tlds:
                tlds.append(tld)
    return TldSelection(tuple(tlds), tuple(rejected))


class BrandCandidates:
    """Builds the candidate list for a name (see the module docstring).

    ``execute`` returns candidates in check order: exact names (request
    order), then hacks, then variants; each fqdn appears once (first kind
    wins). After it runs, ``skipped`` counts invalid candidates,
    ``suffix_hack_found`` says whether the name itself ends in a known TLD,
    and ``unusable_suffixes`` lists known suffixes ``TLD`` cannot represent.
    """

    def __init__(
        self,
        name: str,
        tlds: Sequence[TLD],
        known_tlds: KnownTlds,
        *,
        variants: bool = False,
        hack_words: Sequence[HackWord] = (),
    ) -> None:
        self.name = DomainHack._normalize(name)
        self._tlds = tuple(tlds)
        self._known = known_tlds
        self._variants = variants
        self._hack_words = tuple(hack_words)
        self.skipped = 0
        self.suffix_hack_found = False
        self.unusable_suffixes: list[str] = []

    def execute(self) -> list[BrandCandidate]:
        self.skipped = 0
        self.suffix_hack_found = False
        self.unusable_suffixes = []
        seen: set[str] = set()
        out: list[BrandCandidate] = []
        for sld, tld, kind, label, meaning in self._raw():
            try:
                domain = DomainHack.from_sld(sld, tld)
            except InvalidLabelError:
                self.skipped += 1
                continue
            if domain.fqdn in seen:
                continue
            seen.add(domain.fqdn)
            out.append(BrandCandidate(domain, kind, label, meaning))
        return out

    def _raw(self) -> Iterable[tuple[str, TLD, CandidateKind, str, str]]:
        name = self.name
        for tld in self._tlds:
            yield name, tld, CandidateKind.EXACT, name, ""
        for sld, tld in self._split(name, min_tail=0):
            self.suffix_hack_found = True
            yield sld, tld, CandidateKind.HACK, name, ""
        for hw in self._hack_words:
            # The TLD must lie within the extra word, so the name stays whole.
            for sld, tld in self._split(name + hw.word, min_tail=len(hw.word)):
                yield sld, tld, CandidateKind.HACK, f"{name} {hw.word}", hw.meaning
        if self._variants:
            for tld in self._variant_tlds():
                for prefix in VARIANT_PREFIXES:
                    yield prefix + name, tld, CandidateKind.VARIANT, f"{prefix} {name}", ""
                for suffix in VARIANT_SUFFIXES:
                    yield name + suffix, tld, CandidateKind.VARIANT, f"{name} {suffix}", ""

    def _split(self, text: str, *, min_tail: int) -> Iterable[tuple[str, TLD]]:
        """(sld, tld) for each known suffix of ``text`` that fits in its last ``min_tail``
        characters (any length when 0)."""
        for suffix in self._known.suffixes_of(text):
            letters = len(suffix.replace(".", ""))
            if min_tail and letters > min_tail:
                continue
            try:
                tld = TLD(suffix)
            except ValueError:
                if suffix not in self.unusable_suffixes:
                    self.unusable_suffixes.append(suffix)
                continue
            yield text[:-letters], tld

    def _variant_tlds(self) -> list[TLD]:
        base = TLD(VARIANT_BASE_TLD)
        others = [t for t in self._tlds if t != base][:VARIANT_TLD_LIMIT]
        return [base, *others]
