"""``domainhack watch``: the watchlist, the comparison between checks, and the command."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from domainhack.adapters.watch_store import (
    WatchEntry,
    WatchlistError,
    WatchStore,
    default_watchlist_path,
)
from domainhack.cli.app import EXIT_FAILURE, EXIT_OK, EXIT_USAGE, main
from domainhack.cli.watch_cmd import EXIT_CHANGED
from domainhack.domain.entities import TLD, Availability, DomainCheckResult, DomainHack
from domainhack.usecases.watch import (
    AVAILABLE,
    DROPPING,
    TAKEN,
    EventKind,
    WatchState,
    compare,
    state_of,
)
from tests.fakes import Answer, FakeCatalog, ScriptedRegistrar

EXPIRES = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)
RENEWED = datetime(2027, 11, 12, 8, 0, tzinfo=timezone.utc)
TAKEN_ANSWER = Answer(expires_at=EXPIRES)
DROPPING_ANSWER = Answer(statuses=("pending delete",), expires_at=EXPIRES)


def _result(answer: Answer | Availability) -> DomainCheckResult:
    registrar = ScriptedRegistrar(default=answer)
    return registrar.check_availability(DomainHack.from_sld("pla", TLD("to")))


class TestState:
    def test_available(self) -> None:
        assert state_of(_result(Availability.AVAILABLE)) == WatchState(AVAILABLE)

    def test_taken_with_details(self) -> None:
        state = state_of(_result(Answer(expires_at=EXPIRES, parked_hint="sedo")))
        assert state == WatchState(TAKEN, (), "2026-11-12", "sedo")
        assert state is not None
        assert state.describe() == "taken, expires 2026-11-12, parked: sedo"

    def test_dropping(self) -> None:
        state = state_of(_result(Answer(statuses=("client hold", "pending delete"))))
        assert state == WatchState(DROPPING, ("pending delete",))
        assert state is not None
        assert state.describe() == "dropping: pending delete"

    def test_an_error_teaches_nothing(self) -> None:
        assert state_of(_result(Availability.ERROR)) is None


class TestCompare:
    def test_first_check_is_not_a_change(self) -> None:
        (event,) = compare("pla.to", None, WatchState(TAKEN, (), "2026-11-12"))
        assert event.kind is EventKind.FIRST_CHECK
        assert not event.is_change
        assert event.message == "now watching (taken, expires 2026-11-12)"

    def test_nothing_changed(self) -> None:
        state = WatchState(TAKEN, (), "2026-11-12", "sedo")
        assert compare("pla.to", state, state) == []

    @pytest.mark.parametrize(
        ("before", "after", "kind", "message"),
        [
            (
                WatchState(TAKEN, (), "2026-11-12"),
                WatchState(AVAILABLE),
                EventKind.BECAME_AVAILABLE,
                "is available now (was taken, expires 2026-11-12)",
            ),
            (
                WatchState(DROPPING, ("pending delete",)),
                WatchState(AVAILABLE),
                EventKind.BECAME_AVAILABLE,
                "is available now (was dropping: pending delete)",
            ),
            (
                WatchState(TAKEN),
                WatchState(DROPPING, ("redemption period",), "2026-11-12"),
                EventKind.DROPPING,
                "is dropping: redemption period, expires 2026-11-12: it may be free soon",
            ),
            (
                WatchState(AVAILABLE),
                WatchState(TAKEN, (), "2027-01-01"),
                EventKind.REGISTERED,
                "was registered (taken, expires 2027-01-01)",
            ),
            (
                WatchState(DROPPING, ("redemption period",)),
                WatchState(TAKEN, (), "2027-01-01"),
                EventKind.RESTORED,
                "was restored by its holder (taken, expires 2027-01-01)",
            ),
            (
                WatchState(AVAILABLE),
                WatchState(DROPPING, ("pending delete",)),
                EventKind.DROPPING,
                "is dropping: pending delete: it may be free soon",
            ),
        ],
    )
    def test_status_changes(
        self, before: WatchState, after: WatchState, kind: EventKind, message: str
    ) -> None:
        (event,) = compare("pla.to", before, after)
        assert (event.kind, event.message, event.is_change) == (kind, message, True)
        assert (event.before, event.after) == (before, after)

    def test_expiry_and_parking_can_change_together(self) -> None:
        before = WatchState(TAKEN, (), "2026-11-12", "sedo")
        after = WatchState(TAKEN, (), "2027-11-12", "")
        assert [(e.kind, e.message) for e in compare("pla.to", before, after)] == [
            (EventKind.EXPIRY_CHANGED, "expiry date moved: 2026-11-12 -> 2027-11-12"),
            (EventKind.PARKING_CHANGED, "is no longer parked at sedo"),
        ]

    def test_newly_known_expiry_and_parking(self) -> None:
        after = WatchState(TAKEN, (), "2027-11-12", "dan.com")
        assert [e.message for e in compare("pla.to", WatchState(TAKEN), after)] == [
            "expiry date moved: unknown -> 2027-11-12",
            "is now parked at dan.com",
        ]

    def test_an_expiry_the_registry_stopped_showing_is_not_a_change(self) -> None:
        assert compare("pla.to", WatchState(TAKEN, (), "2026-11-12"), WatchState(TAKEN)) == []


class TestStore:
    def test_default_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        assert default_watchlist_path() == tmp_path / "xdg-data" / "domainhack" / "watchlist.json"
        monkeypatch.delenv("XDG_DATA_HOME")
        monkeypatch.setenv("HOME", str(tmp_path))
        expected = tmp_path / ".local" / "share" / "domainhack" / "watchlist.json"
        assert default_watchlist_path() == expected
        monkeypatch.setenv("DOMAINHACK_WATCHLIST", str(tmp_path / "mine.json"))
        assert WatchStore().path == tmp_path / "mine.json"

    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert WatchStore(tmp_path / "w.json").load() == {}

    def test_round_trip_keeps_order_and_idn(self, tmp_path: Path) -> None:
        store = WatchStore(tmp_path / "deep" / "w.json")
        entries = {
            "xn--and-6ma2c.de": WatchEntry("xn--and-6ma2c.de", "ñandú.de", "2026-11-18T10:00:00Z"),
            "pla.to": WatchEntry(
                "pla.to",
                "pla.to",
                "2026-11-18T10:00:00Z",
                "2026-11-19T10:00:00Z",
                WatchState(DROPPING, ("pending delete",), "2026-11-12", "sedo"),
            ),
        }
        store.save(entries)
        loaded = store.load()
        assert loaded == entries
        assert list(loaded) == list(entries)
        assert "ñandú.de" in store.path.read_text(encoding="utf-8")
        assert [p.name for p in store.path.parent.iterdir()] == ["w.json"]  # no temp file left

    @pytest.mark.parametrize(
        "text", ["", "[]", "{}", '{"version": 1}', '{"version": 1, "names": {"a.to": 3}}']
    )
    def test_a_file_that_is_not_a_watchlist(self, tmp_path: Path, text: str) -> None:
        path = tmp_path / "w.json"
        path.write_text(text)
        with pytest.raises(WatchlistError, match="not a domainhack watchlist"):
            WatchStore(path).load()

    def test_a_newer_version_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "w.json"
        path.write_text('{"version": 2, "names": {}}')
        with pytest.raises(WatchlistError, match="version 2 is not supported"):
            WatchStore(path).load()

    def test_unreadable_and_unwritable(self, tmp_path: Path) -> None:
        with pytest.raises(WatchlistError, match="cannot read"):
            WatchStore(tmp_path).load()  # a directory
        blocker = tmp_path / "file"
        blocker.write_text("")
        with pytest.raises(WatchlistError, match="cannot write"):
            WatchStore(blocker / "w.json").save({})

    def test_a_failed_write_leaves_no_temp_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*_args: Any, **_kwargs: Any) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("domainhack.adapters.watch_store.json.dump", boom)
        with pytest.raises(WatchlistError, match="No space left"):
            WatchStore(tmp_path / "w.json").save({})
        assert list(tmp_path.iterdir()) == []


class Watch:
    """Runs ``domainhack watch`` against one scripted registrar and a fixed clock."""

    def __init__(self, tmp_path: Path, script: dict[str, Any] | None = None) -> None:
        self.path = tmp_path / "watch.json"
        self.script: dict[str, Any] = dict(script or {})
        self.registrar = ScriptedRegistrar(self.script)
        self.catalog: FakeCatalog = FakeCatalog(self.registrar)

    def __call__(self, *argv: str) -> int:
        extra = ["--no-progress"] if argv[0] == "run" else []
        self.registrar._script = dict(self.script)
        args = ["watch", *argv, "--watchlist", str(self.path), *extra]
        return main(args, catalog=self.catalog)

    def names(self) -> list[str]:
        return list(WatchStore(self.path).load())


@pytest.fixture
def watch(tmp_path: Path) -> Watch:
    return Watch(tmp_path, {"pla.to": TAKEN_ANSWER, "gus.to": Availability.AVAILABLE})


class TestAddRemoveList:
    def test_add(self, watch: Watch, capsys: pytest.CaptureFixture[str]) -> None:
        assert watch("add", "Pla.TO", "gus.to", "pla.to.") == EXIT_OK
        assert watch.names() == ["pla.to", "gus.to"]
        assert "Watching 2 names (2 added)." in capsys.readouterr().err
        assert watch.registrar.calls == []  # adding asks no registry

    def test_add_again_is_harmless(self, watch: Watch, capsys: pytest.CaptureFixture[str]) -> None:
        watch("add", "pla.to")
        assert watch("add", "pla.to", "gus.to") == EXIT_OK
        err = capsys.readouterr().err
        assert "pla.to is already watched" in err
        assert "Watching 2 names (1 added)." in err

    def test_add_refuses_bad_names_but_keeps_the_good_ones(
        self, watch: Watch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert watch("add", "pla", "bad_name.to", "gus.to") == EXIT_USAGE
        err = capsys.readouterr().err
        assert "error: pla: expected a full domain name like pla.to" in err
        assert "error: bad_name.to:" in err
        assert watch.names() == ["gus.to"]

    def test_add_refuses_a_tld_that_cannot_be_checked(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        watch = Watch(tmp_path)
        watch.catalog = FakeCatalog(by_tld={"to": watch.registrar})
        assert watch("add", "pla.to", "juegos.es") == EXIT_USAGE
        assert "error: juegos.es: " in capsys.readouterr().err
        assert watch.names() == ["pla.to"]

    def test_remove(self, watch: Watch, capsys: pytest.CaptureFixture[str]) -> None:
        watch("add", "pla.to", "gus.to")
        assert watch("remove", "PLA.to") == EXIT_OK
        assert watch.names() == ["gus.to"]
        assert watch("remove", "pla.to", "nope") == EXIT_USAGE
        err = capsys.readouterr().err
        assert "error: pla.to is not watched" in err
        assert "error: nope is not watched" in err

    def test_list(self, watch: Watch, capsys: pytest.CaptureFixture[str]) -> None:
        assert watch("list") == EXIT_OK
        assert "No names are watched yet" in capsys.readouterr().err
        watch("add", "pla.to", "gus.to")
        watch("run")
        capsys.readouterr()
        assert watch("list") == EXIT_OK
        today = datetime.now(timezone.utc).date().isoformat()
        assert capsys.readouterr().out.splitlines() == [
            f"pla.to  taken, expires 2026-11-12  (checked {today})",
            f"gus.to  available  (checked {today})",
        ]

    def test_list_before_the_first_run_and_as_json(
        self, watch: Watch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        watch("add", "pla.to")
        capsys.readouterr()
        watch("list")
        assert capsys.readouterr().out == "pla.to  not checked yet\n"
        watch("list", "--json")
        (item,) = json.loads(capsys.readouterr().out)
        assert (item["domain"], item["state"], item["checked_at"]) == ("pla.to", None, None)

    def test_a_broken_watchlist_is_an_error(
        self, watch: Watch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        watch.path.write_text("nope")
        assert watch("list") == EXIT_FAILURE
        assert "not a domainhack watchlist" in capsys.readouterr().err


class TestRun:
    def test_nothing_to_watch(self, watch: Watch, capsys: pytest.CaptureFixture[str]) -> None:
        assert watch("run") == EXIT_OK
        assert "No names are watched yet" in capsys.readouterr().err

    def test_first_run_records_then_a_quiet_run_prints_nothing(
        self, watch: Watch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        watch("add", "pla.to", "gus.to")
        capsys.readouterr()
        assert watch("run", "--exit-code") == EXIT_OK
        out, err = capsys.readouterr()
        assert out.splitlines() == [
            "pla.to: now watching (taken, expires 2026-11-12)",
            "gus.to: now watching (available)",
        ]
        assert "Checked 2 names: 0 changes, 0 not checked." in err
        assert watch("run", "--exit-code") == EXIT_OK
        assert capsys.readouterr().out == ""

    def test_changes_are_reported_once(
        self, watch: Watch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        watch("add", "pla.to", "gus.to")
        watch("run")
        capsys.readouterr()
        watch.script["pla.to"] = DROPPING_ANSWER
        watch.script["gus.to"] = Answer(expires_at=RENEWED)
        assert watch("run", "--exit-code") == EXIT_CHANGED
        out, err = capsys.readouterr()
        assert out.splitlines() == [
            "pla.to: is dropping: pending delete, expires 2026-11-12: it may be free soon",
            "gus.to: was registered (taken, expires 2027-11-12)",
        ]
        assert "Checked 2 names: 2 changes, 0 not checked." in err
        assert watch("run", "--exit-code") == EXIT_OK
        assert capsys.readouterr().out == ""
        watch.script["pla.to"] = Availability.AVAILABLE
        assert watch("run") == EXIT_OK  # without --exit-code a change is not a failure
        assert (
            capsys.readouterr().out
            == "pla.to: is available now (was dropping: pending delete, expires 2026-11-12)\n"
        )

    def test_json(self, watch: Watch, capsys: pytest.CaptureFixture[str]) -> None:
        watch("add", "pla.to", "gus.to")
        watch("run")
        watch.script["pla.to"] = Availability.AVAILABLE
        watch("add", "abas.to")
        capsys.readouterr()
        assert watch("run", "--format", "json") == EXIT_OK
        data = json.loads(capsys.readouterr().out)
        assert data["checked"] == 3
        assert data["not_checked"] == []
        (change,) = data["changes"]
        assert (change["domain"], change["kind"]) == ("pla.to", "became-available")
        assert change["before"] == {
            "status": "taken",
            "statuses": [],
            "expires_at": "2026-11-12",
            "parked_hint": "",
        }
        assert change["after"]["status"] == "available"
        assert [e["domain"] for e in data["first_checks"]] == ["abas.to"]

    def test_a_failed_check_keeps_the_last_known_state(
        self, watch: Watch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        watch("add", "pla.to", "gus.to")
        watch("run")
        before = WatchStore(watch.path).load()["pla.to"]
        capsys.readouterr()
        watch.script["pla.to"] = Availability.ERROR
        assert watch("run", "--exit-code") == EXIT_FAILURE
        out, err = capsys.readouterr()
        assert out == ""
        assert "warning: pla.to could not be checked: boom" in err
        assert "Checked 2 names: 0 changes, 1 not checked." in err
        assert WatchStore(watch.path).load()["pla.to"] == before
        # The change is still reported when the registry answers again.
        watch.script["pla.to"] = Availability.AVAILABLE
        assert watch("run", "--exit-code") == EXIT_CHANGED

    def test_a_tld_that_stopped_being_checkable(
        self, watch: Watch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        watch("add", "pla.to", "radio.fm")
        watch.catalog = FakeCatalog(by_tld={"to": watch.registrar})
        capsys.readouterr()
        assert watch("run") == EXIT_FAILURE
        out, err = capsys.readouterr()
        assert out == "pla.to: now watching (taken, expires 2026-11-12)\n"
        assert "warning: radio.fm could not be checked: " in err

    def test_every_run_asks_the_registry_again(
        self, watch: Watch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cache = ["--cache-path", str(tmp_path / "cache.sqlite3")]
        watch("add", "pla.to")
        watch("run", *cache)
        watch("run", *cache)
        assert watch.registrar.calls == ["pla.to", "pla.to"]

    def test_idn_names_show_as_written(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        watch = Watch(tmp_path)
        assert watch("add", "ñandú.de") == EXIT_OK
        assert watch.names() == ["xn--and-6ma2c.de"]
        capsys.readouterr()
        watch("run")
        assert capsys.readouterr().out == "ñandú.de: now watching (taken)\n"


def test_help_lists_the_actions(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["watch", "--help"]) == EXIT_OK
    out = capsys.readouterr().out
    assert all(word in out for word in ("add", "remove", "list", "run"))
    assert main(["watch"]) == EXIT_USAGE
