import argparse
import sqlite3
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from domainhack.adapters.cached_registrar import (
    CACHE_RAW_TITLE,
    DEFAULT_TTL_SECONDS,
    CachedRegistrarClient,
    default_cache_path,
)
from domainhack.adapters.rdap_registrar import RdapRegistrarClient
from domainhack.adapters.registrar_router import RegistrarRouter
from domainhack.cli.app import _build_registrar, build_parser
from domainhack.domain.entities import Availability
from tests.fakes import FakeCatalog, FakeClock, ScriptedRegistrar, fake_throttle, hack

NOW = 1_000_000.0


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "nested" / "dir" / "results.sqlite3"


class TestCachedRegistrarClient:
    def test_miss_then_hit(self, db_path: Path) -> None:
        inner = ScriptedRegistrar({"pla.to": Availability.AVAILABLE})
        with CachedRegistrarClient(inner, path=db_path, clock=FakeClock(NOW)) as cached:
            first = cached.check_availability(hack("pla"))
            second = cached.check_availability(hack("pla"))
        assert inner.calls == ["pla.to"]
        assert first.raw_title == "live"
        assert second.availability is Availability.AVAILABLE
        assert second.raw_title == CACHE_RAW_TITLE
        assert second.domain == hack("pla")

    def test_persists_across_instances(self, db_path: Path) -> None:
        clock = FakeClock(NOW)
        inner = ScriptedRegistrar({"pla.to": Availability.TAKEN})
        with CachedRegistrarClient(inner, path=db_path, clock=clock) as cached:
            cached.check_availability(hack("pla"))
        inner2 = ScriptedRegistrar({"pla.to": Availability.TAKEN})
        with CachedRegistrarClient(inner2, path=db_path, clock=clock) as cached:
            result = cached.check_availability(hack("pla"))
        assert inner2.calls == []
        assert result.availability is Availability.TAKEN

    def test_ttl_expiry(self, db_path: Path) -> None:
        clock = FakeClock(NOW)
        inner = ScriptedRegistrar({"pla.to": Availability.TAKEN})
        with CachedRegistrarClient(inner, path=db_path, ttl_seconds=60, clock=clock) as cached:
            cached.check_availability(hack("pla"))
            clock.now += 60
            assert cached.check_availability(hack("pla")).raw_title == CACHE_RAW_TITLE
            clock.now += 1
            assert cached.check_availability(hack("pla")).raw_title == "live"
            # the refreshed entry is served from cache again
            assert cached.check_availability(hack("pla")).raw_title == CACHE_RAW_TITLE
        assert inner.calls == ["pla.to", "pla.to"]

    def test_error_not_cached(self, db_path: Path) -> None:
        inner = ScriptedRegistrar({"pla.to": Availability.ERROR})
        with CachedRegistrarClient(inner, path=db_path, clock=FakeClock(NOW)) as cached:
            assert cached.check_availability(hack("pla")).availability is Availability.ERROR
            cached.check_availability(hack("pla"))
        assert inner.calls == ["pla.to", "pla.to"]
        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("SELECT COUNT(*) FROM results").fetchone() == (0,)
        finally:
            conn.close()

    def test_ignores_unknown_stored_availability(self, db_path: Path) -> None:
        inner = ScriptedRegistrar({"pla.to": Availability.TAKEN})
        clock = FakeClock(NOW)
        with CachedRegistrarClient(inner, path=db_path, clock=clock) as cached:
            cached.check_availability(hack("pla"))
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("UPDATE results SET availability = 'bogus'")
            conn.commit()
        finally:
            conn.close()
        with CachedRegistrarClient(inner, path=db_path, clock=clock) as cached:
            assert cached.check_availability(hack("pla")).raw_title == "live"

    def test_cache_hits_skip_inner_delay(self, db_path: Path) -> None:
        clock = FakeClock(NOW)
        seed = ScriptedRegistrar({"aa.to": Availability.TAKEN, "bb.to": Availability.AVAILABLE})
        with CachedRegistrarClient(seed, path=db_path, clock=clock) as cached:
            cached.check_availability(hack("aa"))
            cached.check_availability(hack("bb"))

        # A real RDAP client with a 5 s per-host delay, throttled on the fake clock.
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(404)

        inner = RdapRegistrarClient(
            "https://rdap.example.test/",
            delay=5.0,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            throttle=fake_throttle(clock),
        )
        with CachedRegistrarClient(inner, path=db_path, clock=clock) as cached:
            assert cached.check_availability(hack("aa")).raw_title == CACHE_RAW_TITLE
            assert cached.check_availability(hack("bb")).raw_title == CACHE_RAW_TITLE
            assert clock.sleeps == []
            assert requests == []
            # Control: a miss does go through the inner client and its throttle.
            cached.check_availability(hack("cc"))
            cached.check_availability(hack("dd"))
        assert len(requests) == 2
        assert clock.sleeps == [5.0]

    def test_corrupt_database_warns_once_and_passes_through(self, tmp_path: Path) -> None:
        path = tmp_path / "results.sqlite3"
        path.write_bytes(b"this is not a sqlite database" * 100)
        inner = ScriptedRegistrar({"a.to": Availability.TAKEN, "b.to": Availability.AVAILABLE})
        warnings: list[str] = []
        with CachedRegistrarClient(inner, path=path, warn=warnings.append) as cached:
            assert cached.check_availability(hack("a")).availability is Availability.TAKEN
            assert cached.check_availability(hack("b")).availability is Availability.AVAILABLE
            assert cached.disabled
        assert inner.calls == ["a.to", "b.to"]
        assert len(warnings) == 1
        assert "result cache disabled" in warnings[0]
        assert str(path) in warnings[0]
        # The unusable file is left untouched.
        assert path.read_bytes().startswith(b"this is not a sqlite database")

    def test_directory_as_path_falls_back(self, tmp_path: Path) -> None:
        inner = ScriptedRegistrar({"a.to": Availability.TAKEN})
        warnings: list[str] = []
        with CachedRegistrarClient(inner, path=tmp_path, warn=warnings.append) as cached:
            assert cached.check_availability(hack("a")).raw_title == "live"
        assert len(warnings) == 1
        assert inner.closed

    def test_parent_is_a_file_falls_back(self, tmp_path: Path) -> None:
        blocker = tmp_path / "file"
        blocker.write_text("x")
        inner = ScriptedRegistrar({"a.to": Availability.TAKEN})
        warnings: list[str] = []
        with CachedRegistrarClient(
            inner, path=blocker / "results.sqlite3", warn=warnings.append
        ) as cached:
            cached.check_availability(hack("a"))
            cached.check_availability(hack("a"))
        assert inner.calls == ["a.to", "a.to"]
        assert len(warnings) == 1

    def test_write_failure_disables_cache(self, db_path: Path) -> None:
        inner = ScriptedRegistrar({"a.to": Availability.TAKEN})
        warnings: list[str] = []
        cached = CachedRegistrarClient(inner, path=db_path, warn=warnings.append)
        with (
            patch.object(cached, "_store", side_effect=sqlite3.OperationalError("disk full")),
            cached,
        ):
            assert cached.check_availability(hack("a")).raw_title == "live"
            assert cached.check_availability(hack("a")).raw_title == "live"
        assert warnings == [
            f"warning: result cache disabled ({db_path}: disk full); continuing without it"
        ]

    def test_default_warning_goes_to_stderr(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        inner = ScriptedRegistrar({"a.to": Availability.TAKEN})
        with CachedRegistrarClient(inner, path=tmp_path) as cached:
            cached.check_availability(hack("a"))
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "warning: result cache disabled" in captured.err

    def test_close_propagates_and_is_idempotent(self, db_path: Path) -> None:
        inner = ScriptedRegistrar({"pla.to": Availability.TAKEN})
        cached = CachedRegistrarClient(inner, path=db_path, clock=FakeClock(NOW))
        cached.check_availability(hack("pla"))
        cached.close()
        assert inner.closed
        cached.close()

    def test_close_without_use_does_not_create_db(self, db_path: Path) -> None:
        inner = ScriptedRegistrar({})
        CachedRegistrarClient(inner, path=db_path).close()
        assert inner.closed
        assert not db_path.parent.exists()

    def test_default_ttl_is_seven_days(self) -> None:
        assert DEFAULT_TTL_SECONDS == 7 * 24 * 3600


class TestDefaultCachePath:
    def test_uses_xdg_cache_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert default_cache_path() == tmp_path / "domainhack" / "results.sqlite3"

    def test_falls_back_to_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        expected = tmp_path / ".cache" / "domainhack" / "results.sqlite3"
        assert default_cache_path() == expected


class TestCliCacheFlags:
    def test_defaults(self) -> None:
        args = build_parser().parse_args(["check", "--range-max", "1"])
        assert args.no_cache is False
        assert args.cache_ttl == 168.0
        assert args.cache_path is None

    def test_flags_parsed(self) -> None:
        args = build_parser().parse_args(
            [
                "check",
                "--range-max",
                "1",
                "--no-cache",
                "--cache-ttl",
                "2.5",
                "--cache-path",
                "/tmp/x.sqlite3",
            ]
        )
        assert args.no_cache is True
        assert args.cache_ttl == 2.5
        assert args.cache_path == Path("/tmp/x.sqlite3")

    def test_build_registrar_wraps_by_default(self, tmp_path: Path) -> None:
        cache_file = tmp_path / "c.sqlite3"
        args = build_parser().parse_args(
            ["check", "--range-max", "1", "--cache-ttl", "2", "--cache-path", str(cache_file)]
        )
        inner = ScriptedRegistrar()
        catalog = FakeCatalog(inner)
        registrar = _build_registrar(args, catalog=catalog)
        assert isinstance(registrar, CachedRegistrarClient)
        with registrar:
            registrar.check_availability(hack("aa"))
            registrar.check_availability(hack("aa"))
        # The cache wraps the TLD router, which asked the catalog for a .to client.
        assert [c.tld.suffix for c in catalog.calls] == ["to"]
        assert inner.calls == ["aa.to"]
        assert cache_file.exists()
        assert inner.closed

    def test_build_registrar_no_cache(self) -> None:
        args = build_parser().parse_args(["check", "--range-max", "1", "--no-cache"])
        inner = ScriptedRegistrar()
        registrar = _build_registrar(args, catalog=FakeCatalog(inner))
        assert isinstance(registrar, RegistrarRouter)
        registrar.check_availability(hack("aa"))
        assert inner.calls == ["aa.to"]

    def test_build_registrar_uses_default_path(self) -> None:
        args = argparse.Namespace(delay=0.0)
        registrar = _build_registrar(args, catalog=FakeCatalog(ScriptedRegistrar()))
        with registrar:
            registrar.check_availability(hack("aa"))
        assert default_cache_path().exists()
