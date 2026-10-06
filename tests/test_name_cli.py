"""``domainhack name`` end to end, with a fake catalog, fake TLD list and fake DNS."""

import csv
import json
from pathlib import Path

import pytest

from domainhack.adapters.dns_resolver import DnsPythonLookup
from domainhack.cli import name_cmd
from domainhack.cli.app import EXIT_FAILURE, EXIT_INTERRUPTED, EXIT_OK, EXIT_USAGE, main
from domainhack.cli.name_cmd import NameServices
from domainhack.domain.entities import Availability, DnsEvidence
from tests.fakes import (
    FakeCatalog,
    FakeDnsLookup,
    FakeKnownTlds,
    Outcome,
    ScriptedRegistrar,
    run_cli,
)

KNOWN = FakeKnownTlds(["com", "app", "io", "ai", "co", "dev", "so", "xyz", "to"])


def _run(
    *args: str,
    script: dict[str, Outcome] | None = None,
    dns: FakeDnsLookup | None = None,
    catalog: FakeCatalog | None = None,
    supported: tuple[str, ...] = (),
) -> int:
    registrar = ScriptedRegistrar(script or {}, default=Availability.AVAILABLE)
    services = NameServices(KNOWN, dns, supported_tlds=lambda: supported)
    return main(
        ["name", *args, "--no-progress", "--no-cache"],
        catalog=catalog if catalog is not None else FakeCatalog(registrar),
        services=services,
    )


def _lines(out: str, name: str) -> list[str]:
    """Report lines for ``name``, with alignment padding collapsed to single spaces."""
    return [" ".join(ln.split()) for ln in out.splitlines() if ln.startswith(f"  {name} ")]


class TestRuns:
    def test_all_ok_prints_report_and_exits_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = _run("sumanda", "--tlds", "com,io", script={"sumanda.com": Availability.TAKEN})
        out, err = capsys.readouterr()
        assert code == EXIT_OK
        assert out.startswith("Domains for 'sumanda'\nTLDs: com, io\n")
        assert "  no TLD is a suffix of 'sumanda'\n" in out
        assert "  1.  sumanda.io" in out
        assert "Done. Checked" in err

    def test_default_preset_is_startup_and_hacks_are_checked(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _run("sumanda") == EXIT_OK
        out = capsys.readouterr().out
        assert "TLDs: com, app, io, ai, co, dev, so, xyz\n" in out
        assert _lines(out, "sumandastud.io") == [
            "sumandastud.io available sumanda studio: a creative or production studio"
        ]

    def test_plato_suffix_hack(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _run("plato", "--tlds", "com") == EXIT_OK
        out = capsys.readouterr().out
        assert "no TLD is a suffix" not in out
        assert _lines(out, "pla.to") == ["pla.to available plato"]

    def test_errors_exit_1_and_still_report(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = _run("sumanda", "--tlds", "com,io", script={"sumanda.io": Availability.ERROR})
        out, err = capsys.readouterr()
        assert code == EXIT_FAILURE
        assert "Could not verify\n  sumanda.io  error  boom\n" in out
        assert "1 errors" in err

    def test_variants(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _run("sumanda", "--tlds", "io", "--variants", "--format", "json") == EXIT_OK
        data = json.loads(capsys.readouterr().out)
        variants = {v["domain"] for v in data["variants"]}
        assert {"getsumanda.com", "sumandahq.io"} <= variants
        assert len(variants) == 2 * 9

    def test_markdown(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _run("sumanda", "--tlds", "com", "--format", "markdown") == EXIT_OK
        assert capsys.readouterr().out.startswith("# Domains for `sumanda`\n")

    def test_interrupt_exits_130_with_partial_report(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = _run(
            "sumanda",
            "--tlds",
            "com,io",
            "--parallel",
            "1",
            script={"sumanda.io": KeyboardInterrupt},
        )
        out, err = capsys.readouterr()
        assert code == EXIT_INTERRUPTED
        assert "sumanda.io   not-checked  not checked (run interrupted)" in out
        assert "run interrupted: some names were not checked" in out
        assert "Interrupted after" in err


class TestTlds:
    def test_unsupported_tlds_are_named_not_silent(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        registrar = ScriptedRegistrar(default=Availability.AVAILABLE)
        catalog = FakeCatalog(by_tld={"com": registrar})
        code = _run("sumanda", "--tlds", "com,xyz", catalog=catalog)
        out, err = capsys.readouterr()
        assert code == EXIT_OK
        assert "warning: no registrar supports .xyz; skipping" in err
        assert "sumanda.xyz  unsupported  no registrar supports .xyz" in out
        assert "sumandastud.io unsupported no registrar supports .io" in _lines(
            out, "sumandastud.io"
        )
        assert "sumanda.xyz" not in registrar.calls

    def test_nothing_supported_exits_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = _run("sumanda", "--tlds", "com", catalog=FakeCatalog(by_tld={}))
        assert code == EXIT_FAILURE
        assert "error: no registrar supports any candidate TLD" in capsys.readouterr().err

    def test_rejected_suffix_warns_and_is_noted(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _run("sumanda", "--tlds", "c0m,io") == EXIT_OK
        out, err = capsys.readouterr()
        assert "warning: .c0m is not a TLD this version can check; skipping" in err
        assert "  - .c0m skipped: not a TLD this version can check\n" in out

    def test_no_valid_tld_is_a_usage_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _run("sumanda", "--tlds", "c0m") == EXIT_USAGE
        assert "error: no valid TLD in --tlds 'c0m'" in capsys.readouterr().err

    def test_all_supported_preset(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _run("sumanda", "--tlds", "all-supported", supported=("to", "io")) == EXIT_OK
        assert "TLDs: io, to\n" in capsys.readouterr().out


class TestNameArgument:
    @pytest.mark.parametrize("name", ["sumanda.com", "two words", "  "])
    def test_bad_names_are_usage_errors(
        self, name: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _run(name) == EXIT_USAGE
        assert "domainhack name: error:" in capsys.readouterr().err

    def test_invalid_label_is_a_usage_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _run("su_manda", "--tlds", "com") == EXIT_USAGE
        assert "error: 'su_manda' is not a valid domain name label" in capsys.readouterr().err

    def test_some_invalid_candidates_are_noted(self, capsys: pytest.CaptureFixture[str]) -> None:
        # "ab" is too short for .it but fine for .com.
        catalog = FakeCatalog(ScriptedRegistrar(default=Availability.AVAILABLE))
        assert _run("ab", "--tlds", "com,it", catalog=catalog) == EXIT_OK
        out, err = capsys.readouterr()
        assert "skipped 1 invalid candidates" in out
        assert "skipped 1 invalid candidates" in err

    def test_unusable_hack_suffix_is_noted(self, capsys: pytest.CaptureFixture[str]) -> None:
        registrar = ScriptedRegistrar(default=Availability.AVAILABLE)
        services = NameServices(FakeKnownTlds(["q", "com"]))
        argv = ["name", "fooq", "--tlds", "com", "--no-progress", "--no-cache"]
        assert main(argv, catalog=FakeCatalog(registrar), services=services) == EXIT_OK
        assert "hack under .q skipped: not a TLD this version can check" in capsys.readouterr().out


class TestDns:
    def test_dns_confirms_by_default_when_available(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        dns = FakeDnsLookup({"sumanda.io": DnsEvidence(nameservers=("ns1.x.net",))})
        script: dict[str, Outcome] = {"sumanda.com": Availability.ERROR}
        assert _run("sumanda", "--tlds", "com,io", script=script, dns=dns) == EXIT_FAILURE
        out = capsys.readouterr().out
        assert "sumanda.com" not in dns.lookups  # errors are not looked up
        assert "sumanda.io" in dns.lookups
        assert "DNS conflicts\n  sumanda.io  registry says available, but DNS delegates" in out

    def test_no_confirm_dns(self, capsys: pytest.CaptureFixture[str]) -> None:
        dns = FakeDnsLookup()
        assert _run("sumanda", "--tlds", "io", "--no-confirm-dns", dns=dns) == EXIT_OK
        assert dns.lookups == []

    def test_confirm_dns_without_resolver_warns(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _run("sumanda", "--tlds", "io", "--confirm-dns") == EXIT_OK
        assert "warning: no DNS resolver available" in capsys.readouterr().err


class TestOutput:
    def test_csv_in_candidate_order(self, tmp_path: Path) -> None:
        out = tmp_path / "sumanda.csv"
        assert _run("sumanda", "--tlds", "com,io", "--output", str(out)) == EXIT_OK
        rows = list(csv.DictReader(out.open(encoding="utf-8")))
        fqdns = [r["fqdn"] for r in rows]
        assert fqdns[:2] == ["sumanda.com", "sumanda.io"]
        assert "sumandastud.io" in fqdns

    def test_jsonl(self, tmp_path: Path) -> None:
        out = tmp_path / "sumanda.jsonl"
        assert _run("sumanda", "--tlds", "com", "--output", str(out)) == EXIT_OK
        first = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
        assert first["fqdn"] == "sumanda.com"

    def test_unknown_extension_is_a_usage_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _run("sumanda", "--output", str(tmp_path / "x.txt")) == EXIT_USAGE
        assert "Cannot infer output format" in capsys.readouterr().err

    def test_unwritable_output_exits_1(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = _run("sumanda", "--output", str(tmp_path / "missing" / "x.csv"))
        assert code == EXIT_FAILURE
        assert "error: cannot write output file" in capsys.readouterr().err


class TestDefaultServices:
    def test_default_services_use_iana_list_and_dns(self) -> None:
        services = name_cmd._default_services()
        assert isinstance(services.dns, DnsPythonLookup)
        known = services.known_tlds
        assert known.is_known("to") and known.is_known(".IO") and known.is_known("it")
        assert not known.is_known("sumanda")
        assert known.suffixes_of("plato") == ["to"]
        assert known.suffixes_of("sumanda") == []
        assert "io" in known.suffixes_of("sumandastudio")


def test_help_smoke() -> None:
    proc = run_cli("name", "--help")
    assert proc.returncode == 0, proc.stderr
    assert "usage: domainhack name" in proc.stdout
    assert "--variants" in proc.stdout and "--confirm-dns" in proc.stdout
    assert "startup, latam, classic, all-supported" in proc.stdout
