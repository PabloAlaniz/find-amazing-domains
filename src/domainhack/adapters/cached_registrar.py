"""SQLite-backed caching decorator for any RegistrarClient."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from domainhack.adapters._registration import normalize_statuses
from domainhack.adapters._stderr import write_stderr
from domainhack.domain.entities import Availability, DomainCheckResult, DomainHack
from domainhack.ports.registrar import RegistrarClient
from domainhack.ports.result_cache import ResultCache

HOUR: float = 60 * 60
DAY: float = 24 * HOUR
AVAILABLE_TTL_SECONDS: float = 24 * HOUR
DROPPING_TTL_SECONDS: float = 24 * HOUR
TAKEN_TTL_SECONDS: float = 30 * DAY
TAKEN_MAX_TTL_SECONDS: float = 90 * DAY
CACHE_RAW_TITLE = "cache"
# Rows are committed in batches: every COMMIT_EVERY writes, when COMMIT_INTERVAL
# seconds have passed since the last commit, and on close(). A killed run loses
# at most one batch; Ctrl-C goes through close() and loses nothing.
COMMIT_EVERY = 25
COMMIT_INTERVAL_SECONDS = 10.0

SCHEMA_VERSION = 1
_COLUMNS = {
    # Version 0 (before statuses/expiration were stored): fqdn, availability, checked_at.
    "statuses": "TEXT NOT NULL DEFAULT ''",
    "expires_at": "REAL",
}

_CACHEABLE = (Availability.AVAILABLE, Availability.TAKEN)


def default_cache_path() -> Path:
    """Return ``$XDG_CACHE_HOME/domainhack/results.sqlite3`` (or ``~/.cache/...``)."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "domainhack" / "results.sqlite3"


@dataclass(frozen=True)
class CacheTtlPolicy:
    """How long a cached result stays fresh, by what it says.

    * AVAILABLE: ``available`` (24 h). Free names are the ones people act on,
      and someone else can register them at any moment.
    * TAKEN and dropping (redemption / pending delete): ``dropping`` (24 h).
    * TAKEN with an expiration date: until that date, at least ``dropping``
      (a name past its expiry is in its grace period and worth a daily look)
      and at most ``taken_max`` (90 days).
    * TAKEN otherwise: ``taken`` (30 days).

    ``cap`` (``--cache-ttl``), when set, caps every one of these.
    """

    available: float = AVAILABLE_TTL_SECONDS
    dropping: float = DROPPING_TTL_SECONDS
    taken: float = TAKEN_TTL_SECONDS
    taken_max: float = TAKEN_MAX_TTL_SECONDS
    cap: float | None = None

    def ttl_for(self, result: DomainCheckResult, checked_at: float) -> float:
        """Seconds a ``result`` checked at ``checked_at`` (epoch seconds) stays fresh."""
        if result.availability is Availability.AVAILABLE:
            ttl = self.available
        elif result.is_dropping:
            ttl = self.dropping
        elif result.expires_at is not None:
            until_expiry = result.expires_at.timestamp() - checked_at
            ttl = min(max(until_expiry, self.dropping), self.taken_max)
        else:
            ttl = self.taken
        return ttl if self.cap is None else min(ttl, self.cap)


class CachedRegistrarClient(RegistrarClient, ResultCache):
    """Decorator that caches AVAILABLE/TAKEN results of an inner RegistrarClient.

    Cache hits never touch the inner client, so any rate-limit delay it applies
    is skipped. ERROR results are never cached. Results served from the cache
    carry ``raw_title == "cache"`` plus the stored ``statuses`` and
    ``expires_at``. How long each result stays fresh is set by ``ttl`` (a
    ``CacheTtlPolicy``); it is decided when the row is read, so a smaller
    ``--cache-ttl`` also applies to rows written by earlier runs.

    The cache is an optimisation, never a reason to fail a run: if the database
    cannot be opened, read or written (corrupt file, a directory, no
    permission...), ``warn`` is called once and every later check goes straight
    to the inner client. Cache files written by older versions are migrated in
    place (missing columns are added; old rows read as "no details").

    It is also a ``ResultCache``: ``lookup`` and ``store`` let a caller that
    queries the inner client itself (the parallel check) use the cache from
    one thread. The sqlite connection is not shared across threads, so all
    calls must come from the same thread.
    """

    def __init__(
        self,
        inner: RegistrarClient,
        path: Path | None = None,
        ttl: CacheTtlPolicy | None = None,
        clock: Callable[[], float] = time.time,
        warn: Callable[[str], None] = write_stderr,
        *,
        commit_every: int = COMMIT_EVERY,
        commit_interval: float = COMMIT_INTERVAL_SECONDS,
    ) -> None:
        self._inner = inner
        self._path = path if path is not None else default_cache_path()
        self._ttl = ttl if ttl is not None else CacheTtlPolicy()
        self._clock = clock
        self._conn: sqlite3.Connection | None = None
        self._warn = warn
        self._disabled = False
        self._commit_every = max(commit_every, 1)
        self._commit_interval = commit_interval
        self._pending = 0
        self._last_commit = 0.0

    @property
    def disabled(self) -> bool:
        """True once the cache failed and the client became a pass-through."""
        return self._disabled

    @property
    def ttl(self) -> CacheTtlPolicy:
        return self._ttl

    def _disable(self, exc: Exception) -> None:
        self._disabled = True
        self._close_db(commit=False)
        self._warn(f"warning: result cache disabled ({self._path}: {exc}); continuing without it")

    def _close_db(self, *, commit: bool) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            if commit and self._pending:
                conn.commit()
        except sqlite3.Error as exc:
            self._warn(f"warning: could not save the result cache ({self._path}: {exc})")
        finally:
            self._pending = 0
            with contextlib.suppress(sqlite3.Error):
                conn.close()

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._path)
            try:
                _migrate(conn)
            except BaseException:
                conn.close()
                raise
            self._conn = conn
            self._last_commit = self._clock()
        return self._conn

    def _lookup(self, domain: DomainHack) -> DomainCheckResult | None:
        row = (
            self._db()
            .execute(
                "SELECT availability, checked_at, statuses, expires_at FROM results WHERE fqdn = ?",
                (domain.fqdn,),
            )
            .fetchone()
        )
        if row is None:
            return None
        availability_value, checked_at, statuses_value, expires_value = row
        try:
            availability = Availability(availability_value)
            checked = float(checked_at)
        except (TypeError, ValueError):
            return None
        if availability not in _CACHEABLE:
            return None
        result = DomainCheckResult(
            domain=domain,
            availability=availability,
            raw_title=CACHE_RAW_TITLE,
            statuses=_load_statuses(statuses_value),
            expires_at=_load_expires(expires_value),
        )
        if self._clock() - checked > self._ttl.ttl_for(result, checked):
            return None
        return result

    def _store(self, result: DomainCheckResult) -> None:
        db = self._db()
        expires = result.expires_at.timestamp() if result.expires_at is not None else None
        db.execute(
            "INSERT OR REPLACE INTO results"
            " (fqdn, availability, checked_at, statuses, expires_at) VALUES (?, ?, ?, ?, ?)",
            (
                result.domain.fqdn,
                result.availability.value,
                self._clock(),
                json.dumps(list(result.statuses)) if result.statuses else "",
                expires,
            ),
        )
        self._pending += 1
        now = self._clock()
        if self._pending >= self._commit_every or now - self._last_commit >= self._commit_interval:
            db.commit()
            self._pending = 0
            self._last_commit = now

    def lookup(self, domain: DomainHack) -> DomainCheckResult | None:
        """A fresh cached result for ``domain``, or None (also when the cache failed)."""
        if self._disabled:
            return None
        try:
            return self._lookup(domain)
        except (sqlite3.Error, OSError) as exc:
            self._disable(exc)
            return None

    def store(self, result: DomainCheckResult) -> None:
        """Save an AVAILABLE/TAKEN ``result``; anything else is ignored."""
        if result.availability not in _CACHEABLE or self._disabled:
            return
        try:
            self._store(result)
        except (sqlite3.Error, OSError) as exc:
            self._disable(exc)

    def check_availability(self, domain: DomainHack) -> DomainCheckResult:
        cached = self.lookup(domain)
        if cached is not None:
            return cached
        result = self._inner.check_availability(domain)
        self.store(result)
        return result

    def close(self) -> None:
        try:
            self._close_db(commit=True)
        finally:
            self._inner.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Create the table, or bring an older one up to ``SCHEMA_VERSION`` in place."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS results ("
        " fqdn TEXT PRIMARY KEY,"
        " availability TEXT NOT NULL,"
        " checked_at REAL NOT NULL)"
    )
    present = {row[1] for row in conn.execute("PRAGMA table_info(results)")}
    for name, declaration in _COLUMNS.items():
        if name not in present:
            conn.execute(f"ALTER TABLE results ADD COLUMN {name} {declaration}")
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    if version < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def _load_statuses(value: object) -> tuple[str, ...]:
    """Stored statuses (a JSON list); ``()`` for old rows or anything malformed."""
    if not isinstance(value, str) or not value:
        return ()
    try:
        parsed = json.loads(value)
    except ValueError:
        return ()
    return normalize_statuses(parsed) if isinstance(parsed, list) else ()


def _load_expires(value: object) -> datetime | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
