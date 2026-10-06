import argparse
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from domainhack.adapters.cached_registrar import (
    CACHE_RAW_TITLE,
    DEFAULT_TTL_SECONDS,
    CachedRegistrarClient,
    default_cache_path,
)
from domainhack.adapters.registrar_router import RegistrarRouter
from domainhack.adapters.tonic_registrar import TonicRegistrarClient
from domainhack.cli.app import _build_registrar, build_parser
from domainhack.domain.entities import TLD, Availability, DomainCheckResult, DomainHack
from domainhack.ports.registrar import RegistrarClient

TO = TLD("to")


class CountingRegistrar(RegistrarClient):
    def __init__(self, availability: dict[str, Availability]) -> None:
        self._availability = availability
        self.calls: list[str] = []
        self.closed = False

    def check_availability(self, domain: DomainHack) -> DomainCheckResult:
        self.calls.append(domain.fqdn)
        return DomainCheckResult(
            domain=domain, availability=self._availability[domain.fqdn], raw_title="live"
        )

    def close(self) -> None:
        self.closed = True


class FakeClock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _hack(sld: str) -> DomainHack:
    return DomainHack.from_sld(sld, TO)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "nested" / "dir" / "results.sqlite3"


class TestCachedRegistrarClient:
    def test_miss_then_hit(self, db_path: Path) -> None:
        inner = CountingRegistrar({"pla.to": Availability.AVAILABLE})
        with CachedRegistrarClient(inner, path=db_path, clock=FakeClock()) as cached:
            first = cached.check_availability(_hack("pla"))
            second = cached.check_availability(_hack("pla"))
        assert inner.calls == ["pla.to"]
        assert first.raw_title == "live"
        assert second.availability is Availability.AVAILABLE
        assert second.raw_title == CACHE_RAW_TITLE
        assert second.domain == _hack("pla")

    def test_persists_across_instances(self, db_path: Path) -> None:
        clock = FakeClock()
        inner = CountingRegistrar({"pla.to": Availability.TAKEN})
        with CachedRegistrarClient(inner, path=db_path, clock=clock) as cached:
            cached.check_availability(_hack("pla"))
        inner2 = CountingRegistrar({"pla.to": Availability.TAKEN})
        with CachedRegistrarClient(inner2, path=db_path, clock=clock) as cached:
            result = cached.check_availability(_hack("pla"))
        assert inner2.calls == []
        assert result.availability is Availability.TAKEN

    def test_ttl_expiry(self, db_path: Path) -> None:
        clock = FakeClock()
        inner = CountingRegistrar({"pla.to": Availability.TAKEN})
        with CachedRegistrarClient(inner, path=db_path, ttl_seconds=60, clock=clock) as cached:
            cached.check_availability(_hack("pla"))
            clock.now += 60
            assert cached.check_availability(_hack("pla")).raw_title == CACHE_RAW_TITLE
            clock.now += 1
            assert cached.check_availability(_hack("pla")).raw_title == "live"
            # the refreshed entry is served from cache again
            assert cached.check_availability(_hack("pla")).raw_title == CACHE_RAW_TITLE
        assert inner.calls == ["pla.to", "pla.to"]

    def test_error_not_cached(self, db_path: Path) -> None:
        inner = CountingRegistrar({"pla.to": Availability.ERROR})
        with CachedRegistrarClient(inner, path=db_path, clock=FakeClock()) as cached:
            assert cached.check_availability(_hack("pla")).availability is Availability.ERROR
            cached.check_availability(_hack("pla"))
        assert inner.calls == ["pla.to", "pla.to"]
        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("SELECT COUNT(*) FROM results").fetchone() == (0,)
        finally:
            conn.close()

    def test_ignores_unknown_stored_availability(self, db_path: Path) -> None:
        inner = CountingRegistrar({"pla.to": Availability.TAKEN})
        clock = FakeClock()
        with CachedRegistrarClient(inner, path=db_path, clock=clock) as cached:
            cached.check_availability(_hack("pla"))
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("UPDATE results SET availability = 'bogus'")
            conn.commit()
        finally:
            conn.close()
        with CachedRegistrarClient(inner, path=db_path, clock=clock) as cached:
            assert cached.check_availability(_hack("pla")).raw_title == "live"

    def test_cache_hits_skip_inner_delay(self, db_path: Path) -> None:
        clock = FakeClock()
        seed = CountingRegistrar({"a.to": Availability.TAKEN, "b.to": Availability.AVAILABLE})
        with CachedRegistrarClient(seed, path=db_path, clock=clock) as cached:
            cached.check_availability(_hack("a"))
            cached.check_availability(_hack("b"))

        inner = TonicRegistrarClient(delay=5.0)
        with (
            patch("domainhack.adapters.tonic_registrar.time.sleep") as sleep,
            patch.object(inner, "check_availability") as inner_check,
            CachedRegistrarClient(inner, path=db_path, clock=clock) as cached,
        ):
            cached.check_availability(_hack("a"))
            cached.check_availability(_hack("b"))
        sleep.assert_not_called()
        inner_check.assert_not_called()

    def test_close_propagates_and_is_idempotent(self, db_path: Path) -> None:
        inner = CountingRegistrar({"pla.to": Availability.TAKEN})
        cached = CachedRegistrarClient(inner, path=db_path, clock=FakeClock())
        cached.check_availability(_hack("pla"))
        cached.close()
        assert inner.closed
        cached.close()

    def test_close_without_use_does_not_create_db(self, db_path: Path) -> None:
        inner = CountingRegistrar({})
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
        inner = MagicMock(spec=RegistrarClient)
        inner.check_availability.return_value = DomainCheckResult(
            domain=_hack("a"), availability=Availability.TAKEN
        )
        # The router creates per-TLD clients lazily, so the patch must cover usage.
        with patch("domainhack.cli.app.TonicRegistrarClient", return_value=inner):
            registrar = _build_registrar(args)
            assert isinstance(registrar, CachedRegistrarClient)
            assert isinstance(registrar._inner, RegistrarRouter)
            with registrar:
                registrar.check_availability(_hack("a"))
                registrar.check_availability(_hack("a"))
        assert inner.check_availability.call_count == 1
        assert cache_file.exists()
        inner.close.assert_called_once()

    def test_build_registrar_no_cache(self) -> None:
        args = build_parser().parse_args(["check", "--range-max", "1", "--no-cache"])
        inner = MagicMock(spec=RegistrarClient)
        with patch("domainhack.cli.app.TonicRegistrarClient", return_value=inner):
            registrar = _build_registrar(args)
            assert isinstance(registrar, RegistrarRouter)
            registrar.check_availability(_hack("a"))
        inner.check_availability.assert_called_once()

    def test_build_registrar_uses_default_path(self) -> None:
        args = argparse.Namespace(delay=0.0)
        inner = MagicMock(spec=RegistrarClient)
        inner.check_availability.return_value = DomainCheckResult(
            domain=_hack("a"), availability=Availability.TAKEN
        )
        with patch("domainhack.cli.app.TonicRegistrarClient", return_value=inner):
            registrar = _build_registrar(args)
            with registrar:
                registrar.check_availability(_hack("a"))
        assert default_cache_path().exists()
