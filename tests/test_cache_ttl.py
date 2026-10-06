"""Split cache TTLs, stored registration details, schema migration and batched commits."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from domainhack.adapters.cached_registrar import (
    CACHE_RAW_TITLE,
    DAY,
    HOUR,
    SCHEMA_VERSION,
    CachedRegistrarClient,
    CacheTtlPolicy,
)
from domainhack.cli.app import _build_registrar, build_parser
from domainhack.domain.entities import Availability, DomainCheckResult
from tests.fakes import Answer, FakeCatalog, FakeClock, ScriptedRegistrar, hack

NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z


def _at(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "results.sqlite3"


def _result(
    availability: Availability = Availability.TAKEN,
    statuses: tuple[str, ...] = (),
    expires_at: datetime | None = None,
) -> DomainCheckResult:
    return DomainCheckResult(
        domain=hack(), availability=availability, statuses=statuses, expires_at=expires_at
    )


class TestTtlPolicy:
    policy = CacheTtlPolicy()

    def test_available(self) -> None:
        assert self.policy.ttl_for(_result(Availability.AVAILABLE), NOW) == 24 * HOUR

    def test_taken_without_expiry(self) -> None:
        assert self.policy.ttl_for(_result(), NOW) == 30 * DAY

    def test_taken_until_expiry(self) -> None:
        result = _result(expires_at=_at(NOW + 10 * DAY))
        assert self.policy.ttl_for(result, NOW) == 10 * DAY

    def test_taken_expiry_capped_at_90_days(self) -> None:
        result = _result(expires_at=_at(NOW + 400 * DAY))
        assert self.policy.ttl_for(result, NOW) == 90 * DAY

    def test_taken_past_expiry_rechecks_daily(self) -> None:
        result = _result(expires_at=_at(NOW - 5 * DAY))
        assert self.policy.ttl_for(result, NOW) == 24 * HOUR

    @pytest.mark.parametrize("status", ["pending delete", "redemption period"])
    def test_dropping(self, status: str) -> None:
        result = _result(statuses=(status,), expires_at=_at(NOW + 60 * DAY))
        assert self.policy.ttl_for(result, NOW) == 24 * HOUR

    def test_cap_applies_to_everything(self) -> None:
        policy = CacheTtlPolicy(cap=2 * HOUR)
        for result in (
            _result(Availability.AVAILABLE),
            _result(),
            _result(expires_at=_at(NOW + 10 * DAY)),
            _result(statuses=("pending delete",)),
        ):
            assert policy.ttl_for(result, NOW) == 2 * HOUR

    def test_cap_larger_than_ttl_changes_nothing(self) -> None:
        policy = CacheTtlPolicy(cap=1000 * DAY)
        assert policy.ttl_for(_result(Availability.AVAILABLE), NOW) == 24 * HOUR
        assert policy.ttl_for(_result(), NOW) == 30 * DAY


class TestTtlWithFakeClock:
    def _age_until_miss(self, answer: Answer, db_path: Path, ages: list[float]) -> list[str]:
        clock = FakeClock(NOW)
        inner = ScriptedRegistrar({"pla.to": answer})
        titles = []
        with CachedRegistrarClient(inner, path=db_path, clock=clock) as cached:
            cached.check_availability(hack())
            for age in ages:
                clock.now = NOW + age
                titles.append(cached.check_availability(hack()).raw_title)
                if titles[-1] == "live":
                    break
        return titles

    def test_available_expires_after_a_day(self, db_path: Path) -> None:
        titles = self._age_until_miss(
            Answer(Availability.AVAILABLE), db_path, [24 * HOUR, 24 * HOUR + 1]
        )
        assert titles == [CACHE_RAW_TITLE, "live"]

    def test_taken_lasts_thirty_days(self, db_path: Path) -> None:
        titles = self._age_until_miss(Answer(), db_path, [29 * DAY, 30 * DAY, 30 * DAY + 1])
        assert titles == [CACHE_RAW_TITLE, CACHE_RAW_TITLE, "live"]

    def test_taken_lasts_until_expiry(self, db_path: Path) -> None:
        answer = Answer(expires_at=_at(NOW + 3 * DAY))
        titles = self._age_until_miss(answer, db_path, [3 * DAY, 3 * DAY + 1])
        assert titles == [CACHE_RAW_TITLE, "live"]

    def test_dropping_expires_after_a_day(self, db_path: Path) -> None:
        answer = Answer(statuses=("pending delete",), expires_at=_at(NOW + 80 * DAY))
        titles = self._age_until_miss(answer, db_path, [HOUR, 24 * HOUR + 1])
        assert titles == [CACHE_RAW_TITLE, "live"]

    def test_cap_applies_to_rows_from_earlier_runs(self, db_path: Path) -> None:
        clock = FakeClock(NOW)
        with CachedRegistrarClient(ScriptedRegistrar(), path=db_path, clock=clock) as cached:
            cached.check_availability(hack())
        clock.now = NOW + 2 * HOUR
        inner = ScriptedRegistrar()
        policy = CacheTtlPolicy(cap=HOUR)
        with CachedRegistrarClient(inner, path=db_path, ttl=policy, clock=clock) as cached:
            assert cached.check_availability(hack()).raw_title == "live"
        assert inner.calls == ["pla.to"]


class TestStoredDetails:
    def test_round_trip(self, db_path: Path) -> None:
        expires = datetime(2027, 3, 1, 12, 30, 15, tzinfo=timezone.utc)
        answer = Answer(statuses=("client hold", "redemption period"), expires_at=expires)
        clock = FakeClock(NOW)
        with CachedRegistrarClient(
            ScriptedRegistrar({"pla.to": answer}), path=db_path, clock=clock
        ) as cached:
            cached.check_availability(hack())
        inner = ScriptedRegistrar()
        with CachedRegistrarClient(inner, path=db_path, clock=clock) as cached:
            result = cached.check_availability(hack())
        assert inner.calls == []
        assert result.raw_title == CACHE_RAW_TITLE
        assert result.statuses == ("client hold", "redemption period")
        assert result.expires_at == expires
        assert result.is_dropping

    @pytest.mark.parametrize(
        ("statuses", "expires_at"),
        [("not json", "x"), ('{"a": 1}', None), ("[1, null]", -1e30), ("", 1e300)],
    )
    def test_malformed_stored_details_are_ignored(
        self, db_path: Path, statuses: str, expires_at: object
    ) -> None:
        clock = FakeClock(NOW)
        with CachedRegistrarClient(ScriptedRegistrar(), path=db_path, clock=clock) as cached:
            cached.check_availability(hack())
        conn = sqlite3.connect(db_path)
        with conn:
            conn.execute("UPDATE results SET statuses = ?, expires_at = ?", (statuses, expires_at))
        conn.close()
        inner = ScriptedRegistrar()
        with CachedRegistrarClient(inner, path=db_path, clock=clock) as cached:
            result = cached.check_availability(hack())
        assert result.raw_title == CACHE_RAW_TITLE
        assert result.availability is Availability.TAKEN
        assert result.statuses == ()
        assert result.expires_at is None

    @pytest.mark.parametrize(
        "update",
        ["checked_at = 'yesterday'", "availability = 'error'"],
    )
    def test_unusable_row_is_a_miss(self, db_path: Path, update: str) -> None:
        clock = FakeClock(NOW)
        with CachedRegistrarClient(ScriptedRegistrar(), path=db_path, clock=clock) as cached:
            cached.check_availability(hack())
        conn = sqlite3.connect(db_path)
        with conn:
            conn.execute(f"UPDATE results SET {update}")
        conn.close()
        with CachedRegistrarClient(ScriptedRegistrar(), path=db_path, clock=clock) as cached:
            assert cached.check_availability(hack()).raw_title == "live"


def _make_v0_db(path: Path, rows: list[tuple[str, str, float]]) -> None:
    """A cache file as written before statuses/expiration were stored."""
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(
            "CREATE TABLE results ("
            " fqdn TEXT PRIMARY KEY,"
            " availability TEXT NOT NULL,"
            " checked_at REAL NOT NULL)"
        )
        conn.executemany("INSERT INTO results VALUES (?, ?, ?)", rows)
    conn.close()


class TestMigration:
    def test_old_rows_keep_working(self, db_path: Path) -> None:
        _make_v0_db(
            db_path,
            [
                ("aa.to", "taken", NOW - 10 * DAY),
                ("bb.to", "available", NOW - HOUR),
                ("cc.to", "available", NOW - 2 * DAY),
            ],
        )
        inner = ScriptedRegistrar({"cc.to": Availability.TAKEN})
        clock = FakeClock(NOW)
        with CachedRegistrarClient(inner, path=db_path, clock=clock) as cached:
            aa = cached.check_availability(hack("aa"))
            bb = cached.check_availability(hack("bb"))
            cc = cached.check_availability(hack("cc"))
        assert (aa.raw_title, aa.availability, aa.statuses, aa.expires_at) == (
            CACHE_RAW_TITLE,
            Availability.TAKEN,
            (),
            None,
        )
        assert (bb.raw_title, bb.availability) == (CACHE_RAW_TITLE, Availability.AVAILABLE)
        assert cc.raw_title == "live"  # an AVAILABLE row older than 24 h is rechecked
        assert inner.calls == ["cc.to"]

        conn = sqlite3.connect(db_path)
        try:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(results)")]
            assert columns == [
                "fqdn",
                "availability",
                "checked_at",
                "statuses",
                "expires_at",
            ]
            assert conn.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        finally:
            conn.close()

    def test_migration_is_idempotent(self, db_path: Path) -> None:
        _make_v0_db(db_path, [])
        for _ in range(2):
            with CachedRegistrarClient(
                ScriptedRegistrar(), path=db_path, clock=FakeClock(NOW)
            ) as cached:
                cached.check_availability(hack())
        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("SELECT COUNT(*) FROM results").fetchone() == (1,)
        finally:
            conn.close()

    def test_newer_user_version_is_left_alone(self, db_path: Path) -> None:
        _make_v0_db(db_path, [])
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA user_version = 99")
        conn.close()
        with CachedRegistrarClient(
            ScriptedRegistrar(), path=db_path, clock=FakeClock(NOW)
        ) as cached:
            cached.check_availability(hack())
            assert not cached.disabled
        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone() == (99,)
        finally:
            conn.close()

    def test_unmigratable_file_disables_cache(self, db_path: Path) -> None:
        conn = sqlite3.connect(db_path)
        with conn:
            conn.execute("CREATE VIEW results AS SELECT 'a.to' AS fqdn")
        conn.close()
        warnings: list[str] = []
        inner = ScriptedRegistrar()
        with CachedRegistrarClient(inner, path=db_path, warn=warnings.append) as cached:
            assert cached.check_availability(hack()).raw_title == "live"
            assert cached.disabled
        assert len(warnings) == 1


def _row_count(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM results").fetchone()
        return int(count)
    finally:
        conn.close()


class TestBatchedCommits:
    def test_commits_every_n_rows_and_on_close(self, db_path: Path) -> None:
        clock = FakeClock(NOW)
        cached = CachedRegistrarClient(
            ScriptedRegistrar(), path=db_path, clock=clock, commit_every=3, commit_interval=1e9
        )
        for sld in ("aa", "bb"):
            cached.check_availability(hack(sld))
        assert _row_count(db_path) == 0  # not committed yet
        cached.check_availability(hack("cc"))
        assert _row_count(db_path) == 3
        cached.check_availability(hack("dd"))
        assert _row_count(db_path) == 3
        cached.close()
        assert _row_count(db_path) == 4

    def test_commits_after_interval(self, db_path: Path) -> None:
        clock = FakeClock(NOW)
        cached = CachedRegistrarClient(
            ScriptedRegistrar(), path=db_path, clock=clock, commit_every=100, commit_interval=10
        )
        cached.check_availability(hack("aa"))
        assert _row_count(db_path) == 0
        clock.now += 10
        cached.check_availability(hack("bb"))
        assert _row_count(db_path) == 2
        cached.close()

    def test_failed_final_commit_warns(self, db_path: Path) -> None:
        warnings: list[str] = []
        cached = CachedRegistrarClient(
            ScriptedRegistrar(),
            path=db_path,
            clock=FakeClock(NOW),
            warn=warnings.append,
            commit_every=100,
            commit_interval=1e9,
        )
        cached.check_availability(hack("aa"))
        real = cached._db()

        class FailingCommit:
            closed = False

            def commit(self) -> None:
                raise sqlite3.OperationalError("disk I/O error")

            def close(self) -> None:
                self.closed = True
                real.close()

        failing = FailingCommit()
        cached._conn = failing  # type: ignore[assignment]
        cached.close()
        assert failing.closed
        assert warnings == [f"warning: could not save the result cache ({db_path}: disk I/O error)"]


class TestCliTtlFlags:
    def _registrar(self, tmp_path: Path, *flags: str) -> CachedRegistrarClient:
        args = build_parser().parse_args(
            ["check", "--range-max", "1", "--cache-path", str(tmp_path / "c.db"), *flags]
        )
        registrar = _build_registrar(args, catalog=FakeCatalog(ScriptedRegistrar()))
        assert isinstance(registrar, CachedRegistrarClient)
        return registrar

    def test_defaults(self, tmp_path: Path) -> None:
        with self._registrar(tmp_path) as registrar:
            assert registrar.ttl == CacheTtlPolicy()

    def test_cache_ttl_caps(self, tmp_path: Path) -> None:
        with self._registrar(tmp_path, "--cache-ttl", "2") as registrar:
            assert registrar.ttl.cap == 2 * HOUR
            assert registrar.ttl.ttl_for(_result(), NOW) == 2 * HOUR

    def test_cache_ttl_available(self, tmp_path: Path) -> None:
        with self._registrar(tmp_path, "--cache-ttl-available", "0.5") as registrar:
            assert registrar.ttl.available == 0.5 * HOUR
            assert registrar.ttl.cap is None

    def test_rejects_negative(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["check", "--range-max", "1", "--cache-ttl-available", "-1"])
        assert "--cache-ttl-available" in capsys.readouterr().err

    def test_help_documents_flags(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["check", "--help"])
        out = capsys.readouterr().out
        assert "--cache-ttl-available HOURS" in out
        assert "--show-dropping" in out
        assert "90 days" in out
