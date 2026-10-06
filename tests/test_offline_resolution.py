"""The backend chosen for a TLD must not depend on the network (audit H3)."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from domainhack.adapters import registrar_catalog
from domainhack.adapters.rdap_bootstrap import (
    RDAP_OVERRIDES,
    RdapBootstrap,
    fetch_iana_bootstrap,
    load_bundled_snapshot,
)
from domainhack.adapters.rdap_registrar import RdapRegistrarClient
from domainhack.adapters.whois_registrar import (
    WHOIS_FALLBACK_SERVERS,
    WHOIS_SERVERS,
    WhoisRegistrarClient,
)
from domainhack.domain.entities import TLD

# Every TLD the catalog knows about by name, plus .ai (bootstrap-only).
CATALOG_TLDS = sorted(
    set(RDAP_OVERRIDES) | set(WHOIS_SERVERS) | set(WHOIS_FALLBACK_SERVERS) | {"ai"}
)

Decision = tuple[str, str]


def _offline() -> bytes:
    raise httpx.ConnectError("network is unreachable")


def _decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fetcher: Callable[[], bytes]
) -> dict[str, Decision]:
    boot = RdapBootstrap(cache_path=tmp_path / "rdap.json", fetcher=fetcher, warn=lambda _: None)
    monkeypatch.setattr(registrar_catalog, "_default_bootstrap", lambda: boot)
    result: dict[str, Decision] = {}
    for suffix in CATALOG_TLDS:
        client = registrar_catalog.build_registrar_for(TLD(suffix), delay=0.0)
        if isinstance(client, RdapRegistrarClient):
            result[suffix] = ("rdap", client.base_url)
        elif isinstance(client, WhoisRegistrarClient):
            result[suffix] = ("whois", "")
        else:
            result[suffix] = ("none", "")
        if client is not None:
            client.close()
    return result


def test_offline_matches_online_for_catalog_tlds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot_bytes = json.dumps(load_bundled_snapshot()).encode()
    online = _decisions(tmp_path / "online", monkeypatch, lambda: snapshot_bytes)
    offline = _decisions(tmp_path / "offline", monkeypatch, _offline)
    assert offline == online
    assert "none" not in {kind for kind, _ in offline.values()}
    # No silent WHOIS fallback for RDAP TLDs in either mode.
    assert "falling back to WHOIS" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("suffix", "kind"),
    [("ai", "rdap"), ("in", "rdap"), ("tv", "rdap"), ("to", "rdap"), ("it", "whois")],
)
def test_offline_audit_examples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str, kind: str
) -> None:
    # Audit H3: offline with an empty cache, .ai was "unsupported" and .in
    # silently switched to WHOIS.
    assert _decisions(tmp_path, monkeypatch, _offline)[suffix][0] == kind


def test_whois_fallback_for_rdap_tld_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    boot = RdapBootstrap(
        cache_path=tmp_path / "rdap.json",
        fetcher=_offline,
        snapshot=lambda: {},
        warn=lambda _: None,
    )
    monkeypatch.setattr(registrar_catalog, "_default_bootstrap", lambda: boot)
    client = registrar_catalog.build_registrar_for(TLD("in"), delay=0.0)
    assert isinstance(client, WhoisRegistrarClient)
    assert client.supports("in")
    assert not client.supports("it")  # only the fallback table
    err = capsys.readouterr().err
    assert "warning: no RDAP server found for .in; falling back to WHOIS" in err


@pytest.mark.integration
def test_live_bootstrap_matches_snapshot_for_catalog_tlds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = fetch_iana_bootstrap()
    online = _decisions(tmp_path / "online", monkeypatch, lambda: live)
    offline = _decisions(tmp_path / "offline", monkeypatch, _offline)
    assert offline == online
