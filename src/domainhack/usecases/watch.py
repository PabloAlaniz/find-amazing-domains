"""Watch names over time: what changed since the last check.

A watched name has a ``WatchState`` (what the registry said last time).
``compare`` turns the previous and the new state into ``WatchEvent`` values:
the name became available, started dropping, was registered or restored, its
expiry date moved, or its parking service changed. The first check of a name
only records where it starts (``FIRST_CHECK``), which is not a change.

Pure: no I/O. ``adapters/watch_store.py`` keeps the states between runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from domainhack.domain.entities import Availability, DomainCheckResult

AVAILABLE = "available"
TAKEN = "taken"
DROPPING = "dropping"


@dataclass(frozen=True)
class WatchState:
    """What a registry said about a name: ``status`` is ``available``, ``taken``
    or ``dropping`` (taken, in redemption or pending delete). ``expires_at`` is
    an ISO 8601 date (``2026-11-12``) or empty when unknown."""

    status: str
    statuses: tuple[str, ...] = ()
    expires_at: str = ""
    parked_hint: str = ""

    def describe(self) -> str:
        """``"taken, expires 2026-11-12, parked: sedo"``."""
        parts = [self.status]
        if self.status == DROPPING and self.statuses:
            parts[0] = f"{DROPPING}: {', '.join(self.statuses)}"
        if self.expires_at:
            parts.append(f"expires {self.expires_at}")
        if self.parked_hint:
            parts.append(f"parked: {self.parked_hint}")
        return ", ".join(parts)


def state_of(result: DomainCheckResult) -> WatchState | None:
    """The state a result shows, or None when the check failed (nothing was learned)."""
    if result.availability is Availability.ERROR:
        return None
    if result.availability is Availability.AVAILABLE:
        return WatchState(AVAILABLE)
    expires = result.expires_at.date().isoformat() if result.expires_at is not None else ""
    if result.is_dropping:
        return WatchState(DROPPING, result.dropping_statuses, expires, result.parked_hint)
    return WatchState(TAKEN, (), expires, result.parked_hint)


class EventKind(Enum):
    FIRST_CHECK = "first-check"
    BECAME_AVAILABLE = "became-available"
    DROPPING = "dropping"
    REGISTERED = "registered"
    RESTORED = "restored"
    EXPIRY_CHANGED = "expiry-changed"
    PARKING_CHANGED = "parking-changed"


@dataclass(frozen=True)
class WatchEvent:
    """One thing to tell the user about a watched name."""

    name: str
    kind: EventKind
    message: str
    before: WatchState | None
    after: WatchState

    @property
    def is_change(self) -> bool:
        """False for the first check of a name, which only records where it starts."""
        return self.kind is not EventKind.FIRST_CHECK


def compare(name: str, before: WatchState | None, after: WatchState) -> list[WatchEvent]:
    """The events between two checks of ``name`` (``before`` None: never checked)."""

    def event(kind: EventKind, message: str) -> WatchEvent:
        return WatchEvent(name, kind, message, before, after)

    if before is None:
        return [event(EventKind.FIRST_CHECK, f"now watching ({after.describe()})")]

    if before.status != after.status:
        was = f"was {before.describe()}"
        if after.status == AVAILABLE:
            return [event(EventKind.BECAME_AVAILABLE, f"is available now ({was})")]
        if after.status == DROPPING:
            return [event(EventKind.DROPPING, f"is {after.describe()}: it may be free soon")]
        if before.status == AVAILABLE:
            return [event(EventKind.REGISTERED, f"was registered ({after.describe()})")]
        return [event(EventKind.RESTORED, f"was restored by its holder ({after.describe()})")]

    events: list[WatchEvent] = []
    if before.expires_at != after.expires_at and after.expires_at:
        moved = f"{before.expires_at or 'unknown'} -> {after.expires_at}"
        events.append(event(EventKind.EXPIRY_CHANGED, f"expiry date moved: {moved}"))
    if before.parked_hint != after.parked_hint:
        if after.parked_hint:
            message = f"is now parked at {after.parked_hint}"
        else:
            message = f"is no longer parked at {before.parked_hint}"
        events.append(event(EventKind.PARKING_CHANGED, message))
    return events
