import json
from pathlib import Path

import httpx
import pytest

from domainhack.adapters.rdap_bootstrap import (
    RDAP_OVERRIDES,
    RdapBootstrap,
    default_bootstrap_cache_path,
    load_bundled_snapshot,
    parse_bootstrap,
    snapshot_date,
)

IANA_DOC = {
    "version": "1.0",
    "services": [
        [["ai", "fm"], ["https://rdap.example.ai/rdap/"]],
        [["in"], ["http://rdap.example.in/", "https://rdap.example.in/"]],
        [["ly"], ["https://rdap.example.ly"]],
        [["gg"], ["https://rdap.gg/"]],
        [["xx"], ["https://rdap.org/"]],
        [["io"], ["https://wrong.example.io/"]],
    ],
}


SNAPSHOT_DOC = {
    "publication": "2026-01-02T03:04:05Z",
    "services": [
        [["ai"], ["https://snapshot.example.ai/"]],
        [["nl"], ["https://snapshot.example.nl/"]],
    ],
}


class Fetcher:
    def __init__(self, payload: bytes | Exception) -> None:
        self.payload = payload
        self.calls = 0

    def __call__(self) -> bytes:
        self.calls += 1
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _bootstrap(
    tmp_path: Path,
    fetcher: Fetcher,
    now: float = 1000.0,
    ttl: float = 100.0,
    warnings: list[str] | None = None,
) -> RdapBootstrap:
    sink = warnings if warnings is not None else []
    return RdapBootstrap(
        cache_path=tmp_path / "rdap.json",
        ttl_seconds=ttl,
        fetcher=fetcher,
        clock=lambda: now,
        snapshot=lambda: SNAPSHOT_DOC,
        warn=sink.append,
    )


def _ok() -> Fetcher:
    return Fetcher(json.dumps(IANA_DOC).encode())


class TestParseBootstrap:
    def test_maps_tlds_and_normalises_urls(self) -> None:
        mapping = parse_bootstrap(IANA_DOC)
        assert mapping["ai"] == "https://rdap.example.ai/rdap/"
        assert mapping["fm"] == "https://rdap.example.ai/rdap/"
        assert mapping["in"] == "https://rdap.example.in/"  # https preferred
        assert mapping["ly"] == "https://rdap.example.ly/"  # trailing slash added
        assert "xx" not in mapping  # rdap.org is never used

    def test_ignores_garbage(self) -> None:
        assert parse_bootstrap({"services": [["bad"], [[1], [2]], "x"]}) == {}
        assert parse_bootstrap([]) == {}


class TestResolution:
    def test_override_wins_without_fetching(self, tmp_path: Path) -> None:
        fetcher = _ok()
        boot = _bootstrap(tmp_path, fetcher)
        assert boot.base_url_for("io") == RDAP_OVERRIDES["io"]
        assert boot.base_url_for("TO") == "https://rdap.tonicregistry.to/rdap/"
        assert fetcher.calls == 0

    def test_denylisted_tld_returns_none(self, tmp_path: Path) -> None:
        fetcher = _ok()
        boot = _bootstrap(tmp_path, fetcher)
        assert boot.base_url_for("gg") is None
        assert boot.base_url_for("la") is None
        assert fetcher.calls == 0

    def test_bootstrap_lookup_and_unknown(self, tmp_path: Path) -> None:
        boot = _bootstrap(tmp_path, _ok())
        assert boot.base_url_for(".ai") == "https://rdap.example.ai/rdap/"
        assert boot.base_url_for("zz") is None

    def test_known_tlds_excludes_denylist(self, tmp_path: Path) -> None:
        tlds = _bootstrap(tmp_path, _ok()).known_tlds()
        assert {"ai", "fm", "io", "co", "to"} <= tlds
        assert "gg" not in tlds


class TestCaching:
    def test_fetch_once_and_write_cache(self, tmp_path: Path) -> None:
        fetcher = _ok()
        boot = _bootstrap(tmp_path, fetcher)
        boot.base_url_for("ai")
        boot.base_url_for("fm")
        assert fetcher.calls == 1
        cached = json.loads((tmp_path / "rdap.json").read_text())
        assert cached["fetched_at"] == 1000.0

    def test_fresh_cache_is_used_by_new_instance(self, tmp_path: Path) -> None:
        _bootstrap(tmp_path, _ok(), now=1000.0).base_url_for("ai")
        fetcher = _ok()
        boot = _bootstrap(tmp_path, fetcher, now=1050.0)
        assert boot.base_url_for("ai") == "https://rdap.example.ai/rdap/"
        assert fetcher.calls == 0

    def test_stale_cache_is_refetched(self, tmp_path: Path) -> None:
        _bootstrap(tmp_path, _ok(), now=1000.0).base_url_for("ai")
        fetcher = _ok()
        _bootstrap(tmp_path, fetcher, now=1200.0).base_url_for("ai")
        assert fetcher.calls == 1

    def test_stale_cache_is_not_used_when_fetch_fails(self, tmp_path: Path) -> None:
        # Offline results must not depend on whatever is left in ~/.cache.
        _bootstrap(tmp_path, _ok(), now=1000.0).base_url_for("ai")
        failing = Fetcher(httpx.ConnectError("offline"))
        warnings: list[str] = []
        boot = _bootstrap(tmp_path, failing, now=5000.0, warnings=warnings)
        assert boot.base_url_for("ai") == "https://snapshot.example.ai/"
        assert boot.base_url_for("fm") is None
        assert len(warnings) == 1

    @pytest.mark.parametrize(
        "payload",
        [httpx.ConnectError("offline"), OSError("boom"), b"not json", b'{"services": []}'],
    )
    def test_failed_fetch_uses_snapshot_and_warns_once(
        self, tmp_path: Path, payload: bytes | Exception
    ) -> None:
        fetcher = Fetcher(payload)
        warnings: list[str] = []
        boot = _bootstrap(tmp_path, fetcher, warnings=warnings)
        assert boot.base_url_for("ai") == "https://snapshot.example.ai/"
        assert boot.base_url_for("fm") is None
        assert boot.base_url_for("io") == RDAP_OVERRIDES["io"]
        assert "ai" in boot.known_tlds()
        assert fetcher.calls == 1  # not retried per TLD
        assert len(warnings) == 1
        assert warnings[0].startswith("could not refresh RDAP bootstrap (")
        assert warnings[0].endswith("; using bundled snapshot from 2026-01-02")

    def test_successful_fetch_does_not_warn(self, tmp_path: Path) -> None:
        warnings: list[str] = []
        _bootstrap(tmp_path, _ok(), warnings=warnings).base_url_for("ai")
        assert warnings == []

    def test_fresh_entries_win_and_snapshot_fills_gaps(self, tmp_path: Path) -> None:
        boot = _bootstrap(tmp_path, _ok())
        assert boot.base_url_for("ai") == "https://rdap.example.ai/rdap/"  # fresh wins
        assert boot.base_url_for("nl") == "https://snapshot.example.nl/"  # snapshot only

    def test_corrupt_cache_triggers_fetch(self, tmp_path: Path) -> None:
        (tmp_path / "rdap.json").write_text("{nope")
        fetcher = _ok()
        assert _bootstrap(tmp_path, fetcher).base_url_for("ai") is not None
        assert fetcher.calls == 1

    def test_unwritable_cache_is_ignored(self, tmp_path: Path) -> None:
        blocker = tmp_path / "file"
        blocker.write_text("x")
        boot = RdapBootstrap(
            cache_path=blocker / "sub" / "rdap.json", fetcher=_ok(), snapshot=lambda: {}
        )
        assert boot.base_url_for("ai") == "https://rdap.example.ai/rdap/"


def test_default_cache_path_uses_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_bootstrap_cache_path() == tmp_path / "domainhack" / "rdap_dns.json"
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert default_bootstrap_cache_path().parts[-3:] == (".cache", "domainhack", "rdap_dns.json")


class TestBundledSnapshot:
    def test_loads_from_package_data(self) -> None:
        data = load_bundled_snapshot()
        services = parse_bootstrap(data)
        # TLDs the catalog serves through the IANA bootstrap (not overrides).
        assert {"ai", "ar", "fm", "in", "is", "ly", "re", "tv"} <= set(services)
        assert snapshot_date(data) != "unknown"

    def test_default_stderr_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        boot = RdapBootstrap(
            cache_path=tmp_path / "rdap.json", fetcher=Fetcher(httpx.ConnectError("offline"))
        )
        assert boot.base_url_for("ai") is not None
        boot.base_url_for("in")
        err = capsys.readouterr().err.splitlines()
        assert len(err) == 1
        date = snapshot_date(load_bundled_snapshot())
        assert err[0] == (
            f"warning: could not refresh RDAP bootstrap (offline); "
            f"using bundled snapshot from {date}"
        )

    @pytest.mark.parametrize("data", [{}, [], {"publication": 5}, {"publication": ""}])
    def test_snapshot_date_unknown(self, data: object) -> None:
        assert snapshot_date(data) == "unknown"
