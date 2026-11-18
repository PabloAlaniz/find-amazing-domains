"""The watchlist file: watched names and what the registry said last time.

A JSON file, by default ``$XDG_DATA_HOME/domainhack/watchlist.json``
(``~/.local/share/domainhack/watchlist.json``). It is rewritten whole and
atomically (a temporary file renamed over it), so an interrupted run never
leaves half a watchlist.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domainhack.usecases.watch import WatchState

WATCHLIST_VERSION = 1
WATCHLIST_ENV = "DOMAINHACK_WATCHLIST"


class WatchlistError(Exception):
    """The watchlist cannot be read or written; the message is meant for the end user."""


@dataclass(frozen=True)
class WatchEntry:
    """A watched name. ``fqdn`` is the ASCII name, ``display`` the name as written.

    ``added_at`` and ``checked_at`` are ISO 8601 UTC date-times; ``checked_at``
    is empty and ``state`` None until the first successful check.
    """

    fqdn: str
    display: str
    added_at: str
    checked_at: str = ""
    state: WatchState | None = None


def default_watchlist_path() -> Path:
    if from_env := os.environ.get(WATCHLIST_ENV):
        return Path(from_env).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "domainhack" / "watchlist.json"


def _state_to_json(state: WatchState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "status": state.status,
        "statuses": list(state.statuses),
        "expires_at": state.expires_at,
        "parked_hint": state.parked_hint,
    }


def _state_from_json(data: Any) -> WatchState | None:
    if data is None:
        return None
    return WatchState(
        status=str(data["status"]),
        statuses=tuple(str(s) for s in data.get("statuses", ())),
        expires_at=str(data.get("expires_at", "")),
        parked_hint=str(data.get("parked_hint", "")),
    )


class WatchStore:
    """Reads and writes the watchlist. Entries keep the order they were added in."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else default_watchlist_path()

    def load(self) -> dict[str, WatchEntry]:
        """Entries by fqdn; empty when the file does not exist yet."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise WatchlistError(f"cannot read {self.path}: {exc.strerror or exc}") from exc
        try:
            data = json.loads(text)
            if data["version"] != WATCHLIST_VERSION:
                raise WatchlistError(
                    f"{self.path}: watchlist version {data['version']!r} is not supported "
                    f"by this version of domainhack (expected {WATCHLIST_VERSION})"
                )
            return {
                str(fqdn): WatchEntry(
                    fqdn=str(fqdn),
                    display=str(item.get("display", fqdn)),
                    added_at=str(item.get("added_at", "")),
                    checked_at=str(item.get("checked_at", "")),
                    state=_state_from_json(item.get("state")),
                )
                for fqdn, item in data["names"].items()
            }
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise WatchlistError(f"{self.path}: not a domainhack watchlist ({exc!r})") from exc

    def save(self, entries: dict[str, WatchEntry]) -> None:
        data = {
            "version": WATCHLIST_VERSION,
            "names": {
                entry.fqdn: {
                    "display": entry.display,
                    "added_at": entry.added_at,
                    "checked_at": entry.checked_at,
                    "state": _state_to_json(entry.state),
                }
                for entry in entries.values()
            },
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".watchlist-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                os.replace(temp, self.path)
            except BaseException:
                Path(temp).unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise WatchlistError(f"cannot write {self.path}: {exc.strerror or exc}") from exc
