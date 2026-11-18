"""Zone snapshots: importing a zone file, looking names up, and saving registry queries."""

from __future__ import annotations

import gzip
import json
import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from domainhack.adapters.zone_cache import ZONE_SOURCE, ZoneAwareCache
from domainhack.adapters.zone_snapshot import (
    ZONE_MAX_AGE_DAYS,
    ZoneError,
    ZoneSnapshot,
    ZoneStore,
    default_zone_dir,
    delegated_labels,
)
from domainhack.cli.app import EXIT_FAILURE, EXIT_OK, EXIT_USAGE, main
from domainhack.cli.name_cmd import NameServices
from domainhack.domain.entities import TLD, Availability, DomainHack
from tests.fakes import FakeCatalog, FakeKnownTlds, FakeResultCache, ScriptedRegistrar

TODAY = date.today()
ZONE = """\
; <<>> DiG 9.18 <<>> @zonedata.example nu AXFR
nu.			7200	IN	SOA	a.example. hostmaster.example. 1 2 3 4 5
nu.			7200	IN	NS	a.example.
me.nu.			86400	IN	NS	ns1.example.net.
me.nu.			86400	IN	NS	ns2.example.net.
Me.NU.			86400	IN	DS	1 13 2 ABCDEF
gus.nu.			86400	IN	NS	ns1.example.net.
ns1.gus.nu.		86400	IN	A	192.0.2.1
deep.sub.nu.		86400	IN	NS	ns1.example.net.
xn--and-6ma2c.nu.	86400	IN	NS	ns1.example.net.
signed.nu.		86400	IN	RRSIG	NS 13 2 86400 20261201000000 20261117000000 1 nu. abc=
other.se.		86400	IN	NS	ns1.example.net.
bad_label.nu.		86400	IN	NS	ns1.example.net.
short.nu. IN NS ns1.example.net.
noclass.nu. 3600 NS ns1.example.net.
;; Query time: 2 msec
"""


@pytest.fixture
def zone_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "zones"
    monkeypatch.setenv("DOMAINHACK_ZONES", str(directory))
    return directory


def _zone_file(tmp_path: Path, text: str = ZONE, name: str = "nu.zone.txt") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestDelegatedLabels:
    def test_master_format(self) -> None:
        assert sorted(set(delegated_labels(ZONE.splitlines(), "nu"))) == [
            "gus",
            "me",
            "noclass",
            "short",
            "xn--and-6ma2c",
        ]

    def test_plain_list(self) -> None:
        lines = ["plato", "Gusto.NU.", "", "# comment", "a b c", "-bad", "other.se"]
        assert list(delegated_labels(lines, "nu")) == ["plato", "gusto"]

    def test_multi_label_suffix(self) -> None:
        lines = ["foto.com.ar. 3600 IN NS ns1.example.", "x.foto.com.ar. 3600 IN NS ns1.example."]
        assert list(delegated_labels(lines, "com.ar")) == ["foto"]


class TestStore:
    def test_default_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        assert default_zone_dir() == tmp_path / "xdg-data" / "domainhack" / "zones"
        monkeypatch.delenv("XDG_DATA_HOME")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert default_zone_dir() == tmp_path / ".local" / "share" / "domainhack" / "zones"
        monkeypatch.setenv("DOMAINHACK_ZONES", str(tmp_path / "z"))
        assert ZoneStore().directory == tmp_path / "z"

    def test_import_and_lookup(self, tmp_path: Path) -> None:
        store = ZoneStore(tmp_path / "zones")
        info = store.import_zone(".NU", _zone_file(tmp_path), TODAY)
        assert (info.suffix, info.names, info.imported) == ("nu", 5, TODAY)
        assert info.path == tmp_path / "zones" / "nu.zone"
        with ZoneSnapshot(info.path) as zone:
            assert zone.info == info
            for label in ("me", "ME", "gus", "short", "noclass", "xn--and-6ma2c"):
                assert label in zone
            for label in ("", "m", "mee", "deep", "signed", "other", "zzz", "aaa", "ñu", 3):
                assert label not in zone
        assert [p.name for p in (tmp_path / "zones").iterdir()] == ["nu.zone"]

    def test_gzip_input(self, tmp_path: Path) -> None:
        source = tmp_path / "nu.zone.gz"
        with gzip.open(source, "wt", encoding="utf-8") as f:
            f.write(ZONE)
        assert ZoneStore(tmp_path / "zones").import_zone("nu", source, TODAY).names == 5

    def test_lookup_agrees_with_a_set_on_a_large_zone(self, tmp_path: Path) -> None:
        rng = random.Random(7)
        alphabet = "abcdefghij0123456789-"
        labels = set()
        while len(labels) < 3000:
            label = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 9))).strip("-")
            if label:
                labels.add(label)
        present = set(rng.sample(sorted(labels), 1500))
        source = _zone_file(tmp_path, "\n".join(sorted(present, key=lambda _: rng.random())))
        info = ZoneStore(tmp_path / "zones").import_zone("nu", source, TODAY)
        with ZoneSnapshot(info.path) as zone:
            assert {label for label in labels if label in zone} == present

    def test_single_name_zone(self, tmp_path: Path) -> None:
        info = ZoneStore(tmp_path / "z").import_zone("nu", _zone_file(tmp_path, "only\n"), TODAY)
        with ZoneSnapshot(info.path) as zone:
            assert "only" in zone
            assert "a" not in zone and "z" not in zone

    @pytest.mark.parametrize(
        ("tld", "text", "message"),
        [
            ("n", "plato\n", "invalid TLD"),
            ("nu", "", "no name delegated under .nu"),
            ("nu", "x.se. 60 IN NS ns.example.\n", "no name delegated under .nu"),
        ],
    )
    def test_import_refuses(self, tmp_path: Path, tld: str, text: str, message: str) -> None:
        store = ZoneStore(tmp_path / "zones")
        with pytest.raises(ZoneError, match=message):
            store.import_zone(tld, _zone_file(tmp_path, text), TODAY)
        assert store.snapshots() == []

    def test_import_errors_name_the_file(self, tmp_path: Path) -> None:
        store = ZoneStore(tmp_path / "zones")
        with pytest.raises(ZoneError, match="cannot read"):
            store.import_zone("nu", tmp_path / "missing.txt", TODAY)
        truncated = tmp_path / "bad.gz"
        truncated.write_bytes(b"\x1f\x8b\x08\x00trunc")
        with pytest.raises(ZoneError, match="cannot read"):
            store.import_zone("nu", truncated, TODAY)
        blocker = tmp_path / "file"
        blocker.write_text("")
        with pytest.raises(ZoneError, match="cannot write"):
            ZoneStore(blocker / "zones").import_zone("nu", _zone_file(tmp_path), TODAY)

    def test_snapshots_skips_what_is_not_one(self, tmp_path: Path) -> None:
        store = ZoneStore(tmp_path / "zones")
        assert store.snapshots() == []
        store.import_zone("nu", _zone_file(tmp_path), TODAY)
        (store.directory / "junk.zone").write_text("hello\n")
        (store.directory / "date.zone").write_text(
            "# domainhack-zone tld=x imported=2026-99-99 names=1\n"
        )
        assert [info.suffix for info in store.snapshots()] == ["nu"]
        with pytest.raises(ZoneError, match="not a domainhack zone snapshot"):
            ZoneSnapshot(store.directory / "junk.zone")
        with pytest.raises(ZoneError, match="cannot read"):
            ZoneSnapshot(store.directory / "missing.zone")

    def test_remove(self, tmp_path: Path) -> None:
        store = ZoneStore(tmp_path / "zones")
        store.import_zone("nu", _zone_file(tmp_path), TODAY)
        assert store.remove(".NU") is True
        assert store.remove("nu") is False

    def test_only_fresh_snapshots_are_opened(self, tmp_path: Path) -> None:
        store = ZoneStore(tmp_path / "zones")
        old = TODAY - timedelta(days=ZONE_MAX_AGE_DAYS + 1)
        store.import_zone("nu", _zone_file(tmp_path), old)
        store.import_zone("se", _zone_file(tmp_path, "plato\n"), TODAY - timedelta(days=7))
        fresh, stale = store.open_fresh(TODAY)
        assert list(fresh) == ["se"]
        assert [info.suffix for info in stale] == ["nu"]
        for zone in fresh.values():
            zone.close()
        # A snapshot dated in the future (a wrong clock) is not trusted either.
        fresh, stale = store.open_fresh(TODAY - timedelta(days=30))
        assert fresh == {} and len(stale) == 2


class TestZoneAwareCache:
    def _zone(self, tmp_path: Path) -> ZoneSnapshot:
        info = ZoneStore(tmp_path / "zones").import_zone("nu", _zone_file(tmp_path), TODAY)
        return ZoneSnapshot(info.path)

    def test_a_name_in_the_zone_is_taken_without_details(self, tmp_path: Path) -> None:
        cache = ZoneAwareCache(None, {"nu": self._zone(tmp_path)})
        hit = cache.lookup(DomainHack.from_sld("me", TLD("nu")))
        assert hit is not None
        assert (hit.availability, hit.raw_title) == (Availability.TAKEN, ZONE_SOURCE)
        assert cache.lookup(DomainHack.from_sld("free", TLD("nu"))) is None
        assert cache.lookup(DomainHack.from_sld("me", TLD("se"))) is None  # no snapshot for .se
        assert cache.zone_hits == 1
        cache.store(hit)  # no result cache: nothing to do
        cache.close()

    def test_the_result_cache_is_asked_first_and_gets_live_results(self, tmp_path: Path) -> None:
        inner = FakeResultCache({"me.nu": Availability.TAKEN})
        cache = ZoneAwareCache(inner, {"nu": self._zone(tmp_path)})
        me, gus = DomainHack.from_sld("me", TLD("nu")), DomainHack.from_sld("gus", TLD("nu"))
        first = cache.lookup(me)
        assert first is not None and first.raw_title != ZONE_SOURCE
        second = cache.lookup(gus)
        assert second is not None and second.raw_title == ZONE_SOURCE
        assert cache.zone_hits == 1
        cache.store(second)
        assert inner.stored == ["gus.nu"]
        cache.close()


def _words(tmp_path: Path) -> Path:
    path = tmp_path / "words.txt"
    path.write_text("menu\ngusnu\nfreenu\n", encoding="utf-8")
    return path


class TestCommands:
    def test_import_list_remove(
        self, tmp_path: Path, zone_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["zones", "list"]) == EXIT_OK
        assert "No zone snapshots yet" in capsys.readouterr().err
        assert main(["zones", "import", "nu", str(_zone_file(tmp_path))]) == EXIT_OK
        assert f"Imported 5 names under .nu into {zone_dir / 'nu.zone'}" in capsys.readouterr().err
        assert main(["zones", "list"]) == EXIT_OK
        assert capsys.readouterr().out == (
            f".nu  5 names  imported {TODAY.isoformat()} (0 days ago, in use)\n"
        )
        assert main(["zones", "remove", ".nu"]) == EXIT_OK
        assert main(["zones", "remove", "nu"]) == EXIT_USAGE
        assert "error: no snapshot for .nu" in capsys.readouterr().err

    def test_zone_dir_option_and_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        other = tmp_path / "elsewhere"
        argv = ["zones", "import", "nu", str(_zone_file(tmp_path)), "--zone-dir", str(other)]
        assert main(argv) == EXIT_OK
        assert (other / "nu.zone").is_file()
        assert main(["zones", "import", "nu", str(tmp_path / "missing")]) == EXIT_FAILURE
        assert "error: cannot read" in capsys.readouterr().err
        assert main(["zones"]) == EXIT_USAGE

    def test_list_marks_old_snapshots(
        self, tmp_path: Path, zone_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ZoneStore(zone_dir).import_zone("nu", _zone_file(tmp_path), TODAY - timedelta(days=30))
        assert main(["zones", "list"]) == EXIT_OK
        assert "(30 days ago, too old, ignored: import it again)" in capsys.readouterr().out


class TestChecksUseSnapshots:
    def _check(self, tmp_path: Path, *extra: str) -> tuple[int, ScriptedRegistrar]:
        registrar = ScriptedRegistrar(default=Availability.AVAILABLE)
        argv = ["--tld", "nu", "check", "--file", str(_words(tmp_path)), "--show-taken"]
        code = main([*argv, "--no-progress", "--no-cache", *extra], catalog=FakeCatalog(registrar))
        return code, registrar

    def test_names_in_the_zone_are_not_queried(
        self, tmp_path: Path, zone_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ZoneStore(zone_dir).import_zone("nu", _zone_file(tmp_path), TODAY)
        code, registrar = self._check(tmp_path)
        assert code == EXIT_OK
        assert registrar.calls == ["free.nu"]
        out, err = capsys.readouterr()
        assert "TAKEN:     me.nu" in out and "TAKEN:     gus.nu" in out
        assert "AVAILABLE: free.nu" in out
        assert "2 names were answered from zone snapshots, without a query." in err
        assert "Checked 3 domains: 1 available, 2 taken" in err

    def test_no_zones_asks_the_registry(
        self, tmp_path: Path, zone_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ZoneStore(zone_dir).import_zone("nu", _zone_file(tmp_path), TODAY)
        _, registrar = self._check(tmp_path, "--no-zones")
        assert sorted(registrar.calls) == ["free.nu", "gus.nu", "me.nu"]
        assert "zone snapshots" not in capsys.readouterr().err

    def test_a_stale_snapshot_is_ignored_with_a_warning(
        self, tmp_path: Path, zone_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        old = TODAY - timedelta(days=ZONE_MAX_AGE_DAYS + 3)
        ZoneStore(zone_dir).import_zone("nu", _zone_file(tmp_path), old)
        _, registrar = self._check(tmp_path)
        assert len(registrar.calls) == 3
        err = capsys.readouterr().err
        assert f"warning: the zone snapshot of .nu is from {old.isoformat()}" in err

    def test_an_unreadable_snapshot_directory_does_not_stop_the_run(
        self,
        tmp_path: Path,
        zone_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def boom(self: ZoneStore, today: date) -> None:
            raise ZoneError("cannot read the zones")

        monkeypatch.setattr(ZoneStore, "open_fresh", boom)
        code, registrar = self._check(tmp_path)
        assert code == EXIT_OK and len(registrar.calls) == 3
        assert "warning: zone snapshots not used: cannot read the zones" in capsys.readouterr().err

    def test_name_command_uses_them(
        self, tmp_path: Path, zone_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ZoneStore(zone_dir).import_zone("nu", _zone_file(tmp_path), TODAY)
        registrar = ScriptedRegistrar(default=Availability.AVAILABLE)
        services = NameServices(FakeKnownTlds(["nu", "com"]), None, supported_tlds=lambda: ())
        argv = ["name", "gus", "--tlds", "nu,com", "--format", "json", "--no-progress"]
        code = main([*argv, "--no-cache"], catalog=FakeCatalog(registrar), services=services)
        assert code == EXIT_OK
        assert "gus.nu" not in registrar.calls and "gus.com" in registrar.calls
        data = json.loads(capsys.readouterr().out)
        assert [(e["domain"], e["status"]) for e in data["exact"]] == [
            ("gus.nu", "taken"),
            ("gus.com", "available"),
        ]

    def test_watch_always_asks_the_registry(self, tmp_path: Path, zone_dir: Path) -> None:
        ZoneStore(zone_dir).import_zone("nu", _zone_file(tmp_path), TODAY)
        registrar = ScriptedRegistrar()
        catalog = FakeCatalog(registrar)
        watchlist = ["--watchlist", str(tmp_path / "w.json")]
        assert main(["watch", "add", "me.nu", *watchlist], catalog=catalog) == EXIT_OK
        assert main(["watch", "run", "--no-progress", *watchlist], catalog=catalog) == EXIT_OK
        assert registrar.calls == ["me.nu"]
