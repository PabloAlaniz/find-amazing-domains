"""``domainhack tlds`` and ``describe_backend``: coverage, offline."""

import json

import pytest

from domainhack.adapters.rdap_bootstrap import RdapBootstrap
from domainhack.adapters.registrar_catalog import describe_backend
from domainhack.cli.app import main
from domainhack.cli.tlds_cmd import _offline_bootstrap
from tests.fakes import run_cli


@pytest.fixture
def offline() -> RdapBootstrap:
    return _offline_bootstrap()


class TestDescribeBackend:
    def test_rdap_override_reports_host_and_verification(self, offline: RdapBootstrap) -> None:
        backend = describe_backend("to", offline)
        assert (backend.kind, backend.host) == ("rdap", "rdap.tonicregistry.to")
        assert backend.detail == "2026-10-06"

    def test_rdap_from_bootstrap(self, offline: RdapBootstrap) -> None:
        backend = describe_backend("ad", offline)
        assert (backend.kind, backend.detail) == ("rdap", "IANA RDAP bootstrap")

    def test_whois_and_multi_label(self, offline: RdapBootstrap) -> None:
        assert describe_backend("it", offline).host == "whois.nic.it"
        assert describe_backend(".COM.AR", offline).host == "rdap.nic.ar"

    def test_unsupported_without_known_reason(self, offline: RdapBootstrap) -> None:
        backend = describe_backend("zz", offline)
        assert (backend.kind, backend.host, backend.detail) == (
            "unsupported",
            "",
            "no source known",
        )


class TestCommand:
    def test_summary_and_cctld_table(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["tlds"]) == 0
        out = capsys.readouterr().out.splitlines()
        assert out[0].endswith(")") and " of " in out[0] and "TLDs can be checked" in out[0]
        assert any(line.startswith("  .to ") for line in out)
        assert not any(line.startswith("  .com ") for line in out)  # ccTLDs only

    def test_all_includes_generic_tlds(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["tlds", "--all"]) == 0
        assert any(line.startswith("  .com ") for line in capsys.readouterr().out.splitlines())

    def test_only_selected_without_summary(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["tlds", "to", ".it"]) == 0
        lines = capsys.readouterr().out.splitlines()
        assert [line.split()[0] for line in lines] == [".to", ".it"]

    def test_unsupported_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["tlds", "--unsupported", "--json", "to", "zw"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert [entry["tld"] for entry in data] == [
            entry["tld"] for entry in data if entry["kind"] == "unsupported"
        ]
        assert "to" not in [entry["tld"] for entry in data]

    def test_unknown_tld_is_usage_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["tlds", "zz"]) == 2
        assert "not a known TLD: zz" in capsys.readouterr().err


def test_subprocess_is_offline_and_works() -> None:
    proc = run_cli("tlds", "to")
    assert proc.returncode == 0, proc.stderr
    assert "rdap.tonicregistry.to" in proc.stdout
