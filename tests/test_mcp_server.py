"""The MCP server: its tools call the same use cases as the CLI, with fakes behind them."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from domainhack.cli.name_cmd import NameServices
from domainhack.domain.entities import Availability, DnsEvidence
from domainhack.mcp import server as mcp_server
from domainhack.mcp.server import build_server, parse_domains, run_options
from tests.fakes import Answer, FakeCatalog, FakeDnsLookup, FakeKnownTlds, ScriptedRegistrar

KNOWN = FakeKnownTlds(["com", "app", "io", "ai", "co", "dev", "so", "xyz", "to", "ing", "com.ar"])
SUPPORTED = ("com", "app", "io", "ai", "co", "dev", "so", "xyz", "to", "ing", "ar")


def _services(dns: FakeDnsLookup | None = None) -> NameServices:
    return NameServices(KNOWN, dns, supported_tlds=lambda: SUPPORTED)


def _server(
    script: dict[str, Any] | None = None,
    *,
    dns: FakeDnsLookup | None = None,
    catalog: FakeCatalog | None = None,
) -> tuple[Any, ScriptedRegistrar]:
    registrar = ScriptedRegistrar(script or {}, default=Availability.AVAILABLE)
    server = build_server(
        run=run_options(["--no-cache"]),
        catalog=catalog if catalog is not None else FakeCatalog(registrar),
        services=_services(dns),
    )
    return server, registrar


def _call(server: Any, tool: str, **arguments: Any) -> Any:
    async def go() -> Any:
        return await server.call_tool(tool, arguments)

    return anyio.run(go)


def _data(server: Any, tool: str, **arguments: Any) -> dict[str, Any]:
    result = _call(server, tool, **arguments)
    assert not result.is_error, result.content
    data = result.structured_content
    assert isinstance(data, dict)
    return data


class TestServer:
    def test_lists_four_tools_with_schemas(self) -> None:
        server, _ = _server()
        tools = anyio.run(server.list_tools)
        by_name = {t.name: t for t in tools}
        assert set(by_name) == {
            "find_domain_for_name",
            "check_domains",
            "find_hacks_for_words",
            "list_tlds",
        }
        assert by_name["find_domain_for_name"].input_schema["required"] == ["name"]
        assert by_name["check_domains"].input_schema["required"] == ["domains"]
        assert "startup" in (by_name["find_domain_for_name"].description or "")

    def test_run_options_default_to_the_cli_defaults_without_progress(self) -> None:
        run = run_options([])
        assert run.delay == 1.0
        assert run.parallel == 4
        assert run.no_progress is True
        assert run.no_cache is False
        run = run_options(["--delay", "0", "--parallel", "2", "--no-cache"])
        assert (run.delay, run.parallel, run.no_cache, run.no_progress) == (0.0, 2, True, True)

    def test_version_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            run_options(["--version"])
        assert exc.value.code == 0
        assert "domainhack-mcp" in capsys.readouterr().out

    def test_main_runs_the_server_over_stdio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[tuple[str, Any]] = []

        class FakeServer:
            def run(self, transport: str) -> None:
                calls.append(("run", transport))

        def fake_build(*, run: Any) -> FakeServer:
            calls.append(("build", run.delay))
            return FakeServer()

        monkeypatch.setattr(mcp_server, "build_server", fake_build)
        assert mcp_server.main(["--delay", "0.5"]) == 0
        assert calls == [("build", 0.5), ("run", "stdio")]


class TestFindDomainForName:
    def test_returns_the_report_with_recommendation_and_summary(self) -> None:
        server, registrar = _server({"sumanda.com": Availability.TAKEN})
        data = _data(server, "find_domain_for_name", name="Sumanda", tlds="com,io")
        assert data["name"] == "sumanda"
        assert data["tlds"] == ["com", "io"]
        assert [e["status"] for e in data["exact"]] == ["taken", "available"]
        assert data["recommendations"][0]["domain"] == "sumanda.io"
        assert data["summary"]["taken"] == 1
        assert data["summary"]["interrupted"] is False
        assert data["warnings"] == []
        assert registrar.calls.count("sumanda.com") == 1

    def test_hacks_and_variants(self) -> None:
        server, _ = _server()
        data = _data(server, "find_domain_for_name", name="plato", tlds="com", variants=True)
        assert data["suffix_hack_found"] is True
        assert any(h["domain"] == "pla.to" for h in data["hacks"])
        assert data["variants_requested"] is True
        assert {v["domain"] for v in data["variants"]} >= {"getplato.com", "platohq.com"}

    def test_dns_conflicts_and_confirm_dns_off(self) -> None:
        dns = FakeDnsLookup({"sumanda.io": DnsEvidence(nameservers=("ns1.example.net",))})
        server, _ = _server(dns=dns)
        data = _data(server, "find_domain_for_name", name="sumanda", tlds="io")
        assert [e["domain"] for e in data["dns_conflicts"]] == ["sumanda.io"]
        assert data["recommendations"] == [] or all(
            r["domain"] != "sumanda.io" for r in data["recommendations"]
        )
        dns.calls.clear()
        data = _data(server, "find_domain_for_name", name="sumanda", tlds="io", confirm_dns=False)
        assert data["dns_conflicts"] == []
        assert dns.calls == []

    def test_warnings_are_returned_not_printed(self, capsys: pytest.CaptureFixture[str]) -> None:
        server, _ = _server()
        data = _data(server, "find_domain_for_name", name="sumanda", tlds="c0m,io")
        assert "warning: .c0m is not a TLD this version can check; skipping" in data["warnings"]
        assert any("c0m" in note for note in data["notes"])
        assert capsys.readouterr().err == ""

    def test_unsupported_tld_is_reported_in_the_data(self) -> None:
        registrar = ScriptedRegistrar(default=Availability.AVAILABLE)
        server, _ = _server(catalog=FakeCatalog(by_tld={"com": registrar}))
        data = _data(server, "find_domain_for_name", name="sumanda", tlds="com,xyz")
        unverifiable = {e["domain"]: e["status"] for e in data["unverifiable"]}
        assert unverifiable["sumanda.xyz"] == "unsupported"
        assert any("no registrar supports .xyz" in w for w in data["warnings"])

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({"name": "sum anda"}, "not a single name"),
            ({"name": "sumanda.com"}, "without a TLD"),
            ({"name": "sumanda", "tlds": "c0m"}, "no valid TLD"),
        ],
    )
    def test_bad_requests_are_tool_errors(self, arguments: dict[str, Any], message: str) -> None:
        server, _ = _server()
        with pytest.raises(ToolError, match=message):
            _call(server, "find_domain_for_name", **arguments)

    def test_nothing_supported_is_a_tool_error(self) -> None:
        server, _ = _server(catalog=FakeCatalog(by_tld={}))
        with pytest.raises(ToolError, match="no registrar supports any candidate TLD"):
            _call(server, "find_domain_for_name", name="sumanda", tlds="com")

    def test_taken_details_are_included(self) -> None:
        expires = datetime(2026, 11, 12, tzinfo=timezone.utc)
        answer = Answer(
            Availability.TAKEN,
            statuses=("pending delete",),
            expires_at=expires,
            registrar="Example Registrar",
        )
        server, _ = _server({"sumanda.com": answer})
        data = _data(server, "find_domain_for_name", name="sumanda", tlds="com")
        taken = data["taken"][0]
        assert taken["status"] == "dropping"
        assert taken["statuses"] == ["pending delete"]
        assert taken["expires_at"].startswith("2026-11-12")
        assert taken["registrar"] == "Example Registrar"


class TestCheckDomains:
    def test_checks_valid_names_and_reports_the_rest(self) -> None:
        registrar = ScriptedRegistrar(
            {"pla.to": Availability.TAKEN}, default=Availability.AVAILABLE
        )
        server, _ = _server(catalog=FakeCatalog(by_tld={"to": registrar, "io": registrar}))
        data = _data(
            server,
            "check_domains",
            domains=["pla.to", "Pla.To.", "test.io", "gus.xyz", "nodot", "bad name.io"],
        )
        results = {r["fqdn"]: r for r in data["results"]}
        assert set(results) == {"pla.to", "test.io"}
        assert results["pla.to"]["availability"] == "taken"
        assert results["test.io"]["availability"] == "available"
        assert registrar.calls.count("pla.to") == 1
        assert data["unsupported"] == [
            {"domain": "gus.xyz", "reason": "no registrar supports .xyz"}
        ]
        assert [r["domain"] for r in data["rejected"]] == ["nodot", "bad name.io"]
        assert data["summary"]["checked"] == 2

    def test_dns_evidence(self) -> None:
        dns = FakeDnsLookup({"test.io": DnsEvidence(nameservers=("ns1.example.net",))})
        server, _ = _server(dns=dns)
        data = _data(server, "check_domains", domains=["test.io"])
        (result,) = data["results"]
        assert result["dns_nameservers"] == ["ns1.example.net"]
        assert result["dns_conflict"] is True
        data = _data(server, "check_domains", domains=["test.io"], confirm_dns=False)
        assert data["results"][0]["dns_nameservers"] == []

    def test_only_unsupported_names_checks_nothing(self) -> None:
        server, registrar = _server(catalog=FakeCatalog(by_tld={}))
        data = _data(server, "check_domains", domains=["gus.xyz"])
        assert data["results"] == []
        assert data["summary"]["checked"] == 0
        assert data["unsupported"][0]["domain"] == "gus.xyz"
        assert registrar.calls == []

    def test_no_valid_name_is_a_tool_error(self) -> None:
        server, _ = _server()
        with pytest.raises(ToolError, match="no valid domain name"):
            _call(server, "check_domains", domains=["nodot", ""])

    def test_parse_domains_handles_second_level_and_idn(self) -> None:
        domains, rejected = parse_domains(["Foto.com.ar", "ñandú.de", "x"])
        assert [d.fqdn for d in domains] == ["foto.com.ar", "xn--and-6ma2c.de"]
        assert [d.display for d in domains] == ["foto.com.ar", "ñandú.de"]
        assert rejected == [{"domain": "x", "reason": "expected a full domain name like pla.to"}]


class TestOfflineTools:
    def test_find_hacks_for_words(self) -> None:
        server, registrar = _server()
        data = _data(server, "find_hacks_for_words", words=["Plato", "testing", "hello", "to", ""])
        hacks = {(h["word"], h["domain"]): h for h in data["hacks"]}
        assert set(hacks) == {("plato", "pla.to"), ("testing", "test.ing")}
        assert hacks[("plato", "pla.to")]["checkable"] is True
        assert data["words"] == 5
        assert registrar.calls == []
        data = _data(server, "find_hacks_for_words", words=["plato"], min_length=6)
        assert data["hacks"] == []

    def test_list_tlds_supported_and_unsupported(self) -> None:
        server, _ = _server()
        data = _data(server, "list_tlds", only=["cl", "es", "com.ar"])
        by_tld = {t["tld"]: t for t in data["tlds"]}
        assert by_tld["cl"]["kind"] == "whois"
        assert by_tld["com.ar"]["kind"] == "rdap"
        assert "es" not in by_tld  # unsupported, and kind defaults to "supported"
        data = _data(server, "list_tlds", kind="unsupported", only=["cl", "es"])
        assert [t["tld"] for t in data["tlds"]] == ["es"]
        assert "accredited" in data["tlds"][0]["detail"]
        data = _data(server, "list_tlds", kind="all", only=["cl", "es"])
        assert data["count"] == 2

    def test_list_tlds_defaults_to_country_codes(self) -> None:
        server, _ = _server()
        cc = _data(server, "list_tlds")
        assert cc["count"] > 100
        assert all(len(t["tld"]) == 2 for t in cc["tlds"])
        everything = _data(server, "list_tlds", include_generic=True)
        assert everything["count"] > cc["count"]
        assert any(t["tld"] == "com" for t in everything["tlds"])

    def test_list_tlds_unknown_suffix_is_a_tool_error(self) -> None:
        server, _ = _server()
        with pytest.raises(ToolError, match="not a known TLD: zzz"):
            _call(server, "list_tlds", only=["zzz"])
