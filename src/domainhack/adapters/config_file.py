"""The optional configuration file: defaults for options you would otherwise repeat.

```toml
[run]                      # options shared by check, name and domainhack-mcp
delay = 2.0
parallel = 8
contact = "me@example.com"
cache_path = "~/domains/cache.sqlite3"

[presets]                  # extra presets for `name --tlds` (they may replace bundled ones)
mine = ["com", "dev", "com.ar"]

[name]
tlds = "mine,io"
prefixes = ["get", "try"]
suffixes = ["hq", "labs"]

[check]
frequency_list = ["~/words/en-common.txt"]
```

The first of these that applies is used: ``--config PATH``, the
``DOMAINHACK_CONFIG`` environment variable, ``./domainhack.toml``, then
``$XDG_CONFIG_HOME/domainhack/config.toml``. A value on the command line
always wins over the file. Unknown tables and keys are errors, so a typo
never silently does nothing. Relative paths are relative to the file.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10 only
    import tomli as tomllib

from domainhack.adapters.brand_data import parse_presets
from domainhack.usecases.brand_candidates import MAX_AFFIXES, Presets

CONFIG_ENV = "DOMAINHACK_CONFIG"
LOCAL_CONFIG_NAME = "domainhack.toml"


class ConfigError(Exception):
    """The configuration file cannot be used; the message is meant for the end user."""


@dataclass(frozen=True)
class Config:
    """A validated configuration file. Every field is empty when the file does not set it.

    ``run`` maps the shared run options (argparse destinations: ``delay``,
    ``parallel``, ``contact``, ``cache_path``) to their values.
    """

    path: Path | None = None
    run: Mapping[str, Any] = field(default_factory=dict)
    presets: Presets = field(default_factory=dict)
    name_tlds: str | None = None
    name_prefixes: tuple[str, ...] | None = None
    name_suffixes: tuple[str, ...] | None = None
    frequency_lists: tuple[Path, ...] = ()


def default_config_paths() -> list[Path]:
    """Where a configuration file is looked for when none is named, in order."""
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return [Path(LOCAL_CONFIG_NAME), Path(xdg) / "domainhack" / "config.toml"]


def find_config(explicit: Path | None = None) -> Path | None:
    """The file to load: ``explicit``, else ``$DOMAINHACK_CONFIG``, else the first
    default path that exists. A file that was named but does not exist is an error."""
    named = explicit
    if named is None and (from_env := os.environ.get(CONFIG_ENV)):
        named = Path(from_env)
    if named is not None:
        named = named.expanduser()
        if not named.is_file():
            raise ConfigError(f"configuration file not found: {named}")
        return named
    return next((p for p in default_config_paths() if p.is_file()), None)


def _number(minimum: float, kind: type) -> Callable[[Any], Any]:
    def check(value: Any) -> Any:
        ok = isinstance(value, (int, float) if kind is float else int)
        if not ok or isinstance(value, bool) or value < minimum or value != value:
            what = "a number" if kind is float else "an integer"
            raise ValueError(f"expected {what} >= {minimum:g}")
        return kind(value)

    return check


def _string(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected a non-empty string")
    return value.strip()


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError("expected a list of non-empty strings")
    return tuple(v.strip() for v in value)


def _affixes(value: Any) -> tuple[str, ...]:
    items = _strings(value)
    if len(items) > MAX_AFFIXES:
        raise ValueError(f"at most {MAX_AFFIXES} are allowed, got {len(items)}")
    return items


def load_config(
    path: Path | None,
    *,
    contact: Callable[[str], str] = _string,
) -> Config:
    """Read and validate ``path``; an empty ``Config`` when it is None.

    ``contact`` validates ``[run] contact`` (the CLI passes its own check, so
    the file and ``--contact`` accept the same values).
    """
    if path is None:
        return Config()
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: not valid TOML: {exc}") from exc

    base = path.resolve().parent

    def as_path(value: Any) -> Path:
        candidate = Path(_string(value)).expanduser()
        return candidate if candidate.is_absolute() else base / candidate

    def paths(value: Any) -> tuple[Path, ...]:
        if isinstance(value, str):
            return (as_path(value),)
        return tuple(as_path(v) for v in _strings(value))

    def contact_value(value: Any) -> str:
        try:
            return contact(_string(value))
        except Exception as exc:  # the CLI's checker raises argparse.ArgumentTypeError
            raise ValueError(str(exc)) from None

    schema: dict[str, dict[str, Callable[[Any], Any]]] = {
        "run": {
            "delay": _number(0, float),
            "parallel": _number(1, int),
            "contact": contact_value,
            "cache_path": as_path,
        },
        "name": {"tlds": _string, "prefixes": _affixes, "suffixes": _affixes},
        "check": {"frequency_list": paths},
    }
    values: dict[str, dict[str, Any]] = {table: {} for table in schema}
    presets: Presets = {}
    for table, content in data.items():
        if not isinstance(content, dict):
            raise ConfigError(f"{path}: {table!r} must be a table, e.g. [{table}]")
        if table == "presets":
            try:
                presets = parse_presets({k.lower(): v for k, v in content.items()})
            except ValueError as exc:
                raise ConfigError(f"{path}: [presets] {exc}") from None
            continue
        if table not in schema:
            known = ", ".join(["presets", *schema])
            raise ConfigError(f"{path}: unknown table [{table}] (known: {known})")
        for key, value in content.items():
            parse = schema[table].get(key)
            if parse is None:
                known = ", ".join(schema[table])
                raise ConfigError(f"{path}: unknown key {key!r} in [{table}] (known: {known})")
            try:
                values[table][key] = parse(value)
            except ValueError as exc:
                raise ConfigError(f"{path}: [{table}] {key}: {exc}") from None

    name = values["name"]
    return Config(
        path=path,
        run=values["run"],
        presets=presets,
        name_tlds=name.get("tlds"),
        name_prefixes=name.get("prefixes"),
        name_suffixes=name.get("suffixes"),
        frequency_lists=values["check"].get("frequency_list", ()),
    )
