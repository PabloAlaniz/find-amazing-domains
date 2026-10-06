"""SQLite-backed caching decorator for any RegistrarClient."""

from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from domainhack.adapters._stderr import write_stderr
from domainhack.domain.entities import Availability, DomainCheckResult, DomainHack
from domainhack.ports.registrar import RegistrarClient

DEFAULT_TTL_SECONDS: float = 7 * 24 * 60 * 60
CACHE_RAW_TITLE = "cache"

_CACHEABLE = (Availability.AVAILABLE, Availability.TAKEN)


def default_cache_path() -> Path:
    """Return ``$XDG_CACHE_HOME/domainhack/results.sqlite3`` (or ``~/.cache/...``)."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "domainhack" / "results.sqlite3"


class CachedRegistrarClient(RegistrarClient):
    """Decorator that caches AVAILABLE/TAKEN results of an inner RegistrarClient.

    Cache hits never touch the inner client, so any rate-limit delay it applies
    is skipped. ERROR results are never cached. Results served from the cache
    carry ``raw_title == "cache"``.

    The cache is an optimisation, never a reason to fail a run: if the database
    cannot be opened, read or written (corrupt file, a directory, no
    permission...), ``warn`` is called once and every later check goes straight
    to the inner client.
    """

    def __init__(
        self,
        inner: RegistrarClient,
        path: Path | None = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
        warn: Callable[[str], None] = write_stderr,
    ) -> None:
        self._inner = inner
        self._path = path if path is not None else default_cache_path()
        self._ttl = ttl_seconds
        self._clock = clock
        self._conn: sqlite3.Connection | None = None
        self._warn = warn
        self._disabled = False

    @property
    def disabled(self) -> bool:
        """True once the cache failed and the client became a pass-through."""
        return self._disabled

    def _disable(self, exc: Exception) -> None:
        self._disabled = True
        self._close_db()
        self._warn(f"warning: result cache disabled ({self._path}: {exc}); continuing without it")

    def _close_db(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(sqlite3.Error):
                conn.close()

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._path)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS results ("
                " fqdn TEXT PRIMARY KEY,"
                " availability TEXT NOT NULL,"
                " checked_at REAL NOT NULL)"
            )
            self._conn.commit()
        return self._conn

    def _lookup(self, domain: DomainHack) -> DomainCheckResult | None:
        row = (
            self._db()
            .execute(
                "SELECT availability, checked_at FROM results WHERE fqdn = ?",
                (domain.fqdn,),
            )
            .fetchone()
        )
        if row is None:
            return None
        availability_value, checked_at = row
        if self._clock() - float(checked_at) > self._ttl:
            return None
        try:
            availability = Availability(availability_value)
        except ValueError:
            return None
        if availability not in _CACHEABLE:
            return None
        return DomainCheckResult(
            domain=domain, availability=availability, raw_title=CACHE_RAW_TITLE
        )

    def _store(self, result: DomainCheckResult) -> None:
        db = self._db()
        db.execute(
            "INSERT OR REPLACE INTO results (fqdn, availability, checked_at) VALUES (?, ?, ?)",
            (result.domain.fqdn, result.availability.value, self._clock()),
        )
        db.commit()

    def check_availability(self, domain: DomainHack) -> DomainCheckResult:
        if not self._disabled:
            try:
                cached = self._lookup(domain)
            except (sqlite3.Error, OSError) as exc:
                self._disable(exc)
            else:
                if cached is not None:
                    return cached
        result = self._inner.check_availability(domain)
        if result.availability in _CACHEABLE and not self._disabled:
            try:
                self._store(result)
            except (sqlite3.Error, OSError) as exc:
                self._disable(exc)
        return result

    def close(self) -> None:
        try:
            self._close_db()
        finally:
            self._inner.close()
