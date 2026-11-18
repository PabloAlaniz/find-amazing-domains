"""Candidates from a pattern: ``CVCV`` -> ``baba``, ``babe``, ..., ``zuzu``.

A pattern has one character per position of the name. Uppercase letters are
classes, everything else is literal:

- ``C``: a consonant (``b``-``z`` without the vowels; ``y`` counts as one);
- ``V``: a vowel (``a e i o u``);
- ``L``: any letter ``a``-``z``;
- ``D``: a digit ``0``-``9``;
- a lowercase letter, a digit or a hyphen stands for itself (``goCV``,
  ``CVC-app``).

A pattern narrows a brute-force run to names a person can pronounce:
``CVCV`` is 11,025 names where four free letters are 456,976.
"""

from __future__ import annotations

import itertools
import math
import string
from collections.abc import Iterator

from domainhack.domain.label_rules import DNS_MAX_LABEL_LENGTH
from domainhack.ports.word_source import WordSource

VOWELS = "aeiou"
CONSONANTS = "".join(c for c in string.ascii_lowercase if c not in VOWELS)
PATTERN_CLASSES: dict[str, str] = {
    "C": CONSONANTS,
    "V": VOWELS,
    "L": string.ascii_lowercase,
    "D": string.digits,
}
_LITERALS = frozenset(string.ascii_lowercase + string.digits + "-")
MAX_PATTERN_LENGTH = DNS_MAX_LABEL_LENGTH


class PatternError(ValueError):
    """The pattern is not one this module can expand; the message is for the user."""


def parse_pattern(pattern: str) -> tuple[str, ...]:
    """The characters allowed at each position of ``pattern``.

    Raises ``PatternError`` for an empty or too long pattern, an unknown
    character, or hyphens where no domain label may have them (first, last,
    or two in a row, which would produce reserved ``xn--``-style names).
    """
    if not pattern:
        raise PatternError("the pattern is empty")
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise PatternError(f"the pattern is longer than {MAX_PATTERN_LENGTH} characters")
    positions: list[str] = []
    for char in pattern:
        if char in PATTERN_CLASSES:
            positions.append(PATTERN_CLASSES[char])
        elif char in _LITERALS:
            positions.append(char)
        else:
            classes = ", ".join(PATTERN_CLASSES)
            raise PatternError(
                f"unknown character {char!r}: use the classes {classes} (uppercase) "
                "and lowercase letters, digits or hyphens as literals"
            )
    if pattern.startswith("-") or pattern.endswith("-") or "--" in pattern:
        raise PatternError("a hyphen cannot come first, last or twice in a row")
    return tuple(positions)


class PatternWordSource(WordSource):
    """Every name a pattern matches, in order (the first position varies slowest)."""

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self._positions = parse_pattern(pattern)

    def words(self) -> Iterator[str]:
        for combo in itertools.product(*self._positions):
            yield "".join(combo)

    def total(self) -> int:
        """Exact number of words ``words()`` yields, without generating them."""
        return self.count()

    def count(self, min_length: int = 1, prefix: str = "", max_length: int | None = None) -> int:
        """How many words have ``min_length <= len <= max_length`` and start with ``prefix``."""
        length = len(self._positions)
        if length < min_length or (max_length is not None and length > max_length):
            return 0
        if len(prefix) > length:
            return 0
        if any(char not in allowed for char, allowed in zip(prefix, self._positions, strict=False)):
            return 0
        return math.prod(len(allowed) for allowed in self._positions[len(prefix) :])
