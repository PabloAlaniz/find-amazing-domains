"""Bundled data for the ``name`` command: TLD presets and hack words.

Both are JSON objects in ``domainhack/data``; keys starting with ``_`` are
comments and ignored.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from domainhack.usecases.brand_candidates import CATALOG_PRESET, HackWord, Presets

TLD_PRESETS_FILE = "data/tld_presets.json"
HACK_WORDS_FILE = "data/hack_words.json"
DEFAULT_PRESET = "startup"


def _load(path: str) -> dict[str, Any]:
    text = resources.files("domainhack").joinpath(path).read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return {k: v for k, v in data.items() if not k.startswith("_")}


def parse_presets(data: dict[str, Any]) -> Presets:
    """Validate a presets object: each value is a list of suffixes or ``"catalog"``."""
    presets: dict[str, list[str] | str] = {}
    for name, value in data.items():
        if value == CATALOG_PRESET:
            presets[name] = CATALOG_PRESET
        elif isinstance(value, list) and all(isinstance(s, str) for s in value):
            presets[name] = [s.lower() for s in value]
        else:
            raise ValueError(f"preset {name!r}: expected a list of suffixes or {CATALOG_PRESET!r}")
    return presets


def load_tld_presets() -> Presets:
    return parse_presets(_load(TLD_PRESETS_FILE))


def load_hack_words() -> tuple[HackWord, ...]:
    """Hack words in file order."""
    return tuple(
        HackWord(word.lower(), str(meaning)) for word, meaning in _load(HACK_WORDS_FILE).items()
    )
