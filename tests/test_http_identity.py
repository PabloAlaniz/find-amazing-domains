"""Honest User-Agent, optional From header, and the single version source."""

import importlib.metadata
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

import domainhack
from domainhack.adapters import rdap_bootstrap, registrar_catalog
from domainhack.adapters._circuit import HostCircuitBreaker
from domainhack.adapters._http import PROJECT_URL, USER_AGENT, identity_headers
from domainhack.adapters._throttle import HostThrottle
from domainhack.adapters.rdap_bootstrap import RdapBootstrap
from domainhack.adapters.rdap_registrar import RdapRegistrarClient
from domainhack.domain.entities import TLD, DomainHack
from tests.fakes import run_cli

BASE = "https://rdap.example.test/rdap/"


def _captured_request(contact: str | None = None) -> httpx.Request:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(404)

    client = RdapRegistrarClient(
        BASE,
        delay=0.0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        throttle=HostThrottle(clock=lambda: 0.0, sleep=lambda _: None),
        breaker=HostCircuitBreaker(),
        contact=contact,
    )
    with client:
        client.check_availability(DomainHack.from_sld("pla", TLD("io")))
    assert len(seen) == 1
    return seen[0]


class TestUserAgent:
    def test_names_tool_version_and_project(self) -> None:
        assert f"domainhack/{domainhack.__version__} (+{PROJECT_URL})" == USER_AGENT
        assert "Mozilla" not in USER_AGENT

    def test_version_has_a_single_source(self) -> None:
        # pyproject reads the version from domainhack.__version__ (dynamic metadata).
        assert importlib.metadata.version("domainhack") == domainhack.__version__

    def test_identity_headers(self) -> None:
        assert identity_headers() == {"User-Agent": USER_AGENT}
        assert identity_headers("me@example.com") == {
            "User-Agent": USER_AGENT,
            "From": "me@example.com",
        }


class TestRdapHeaders:
    def test_sends_user_agent_and_no_from_by_default(self) -> None:
        request = _captured_request()
        assert request.headers["User-Agent"] == USER_AGENT
        assert request.headers["Accept"] == "application/rdap+json, application/json"
        assert "From" not in request.headers

    def test_sends_from_header_with_contact(self) -> None:
        request = _captured_request(contact="me@example.com")
        assert request.headers["From"] == "me@example.com"
        assert request.headers["User-Agent"] == USER_AGENT

    def test_owned_client_defaults_carry_identity(self) -> None:
        client = RdapRegistrarClient(BASE, contact="me@example.com")
        try:
            assert client.http_client.headers["User-Agent"] == USER_AGENT
            assert client.http_client.headers["From"] == "me@example.com"
        finally:
            client.close()

    def test_catalog_passes_contact(self, tmp_path: Path) -> None:
        boot = RdapBootstrap(
            cache_path=tmp_path / "rdap.json",
            fetcher=lambda: json.dumps({"services": []}).encode(),
        )
        client = registrar_catalog.build_registrar_for(
            TLD("io"), delay=0.0, contact="me@example.com", bootstrap=boot
        )
        assert isinstance(client, RdapRegistrarClient)
        try:
            assert client.http_client.headers["From"] == "me@example.com"
        finally:
            client.close()


class TestBootstrapFetch:
    def test_iana_fetch_sends_user_agent(self) -> None:
        response = MagicMock()
        response.content = b"{}"
        with patch.object(rdap_bootstrap.httpx, "get", return_value=response) as get:
            assert rdap_bootstrap.fetch_iana_bootstrap(timeout=3.0) == b"{}"
        assert get.call_args.kwargs["headers"]["User-Agent"] == USER_AGENT


class TestVersionFlag:
    def test_python_dash_m_version(self) -> None:
        result = run_cli("--version")
        assert result.returncode == 0
        assert result.stdout.strip() == f"domainhack {domainhack.__version__}"
