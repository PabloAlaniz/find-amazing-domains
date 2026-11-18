"""``name --handles``: is the name taken on GitHub, npm and PyPI?"""

from __future__ import annotations

import json
from typing import Any

import anyio
import httpx
import pytest

from domainhack.adapters.handle_http import SERVICES, HttpHandleChecker
from domainhack.cli.app import EXIT_OK, main
from domainhack.cli.name_cmd import NameServices
from domainhack.domain.entities import Availability
from domainhack.mcp.server import build_server, run_options
from domainhack.ports.handle_checker import HandleChecker, HandleResult, HandleStatus
from tests.fakes import FakeCatalog, FakeKnownTlds, ScriptedRegistrar

KNOWN = FakeKnownTlds(["com", "io"])


class FakeHead:
    """Answers HEAD requests from a dict of URL -> status code or exception."""

    def __init__(self, answers: dict[str, int | Exception], default: int = 404) -> None:
        self._answers = answers
        self._default = default
        self.calls: list[str] = []

    def __call__(self, url: str) -> int:
        self.calls.append(url)
        answer = self._answers.get(url, self._default)
        if isinstance(answer, Exception):
            raise answer
        return answer


class TestHttpChecker:
    def test_one_request_per_service_in_order(self) -> None:
        head = FakeHead({"https://registry.npmjs.org/acme": 200})
        results = HttpHandleChecker(head=head).check(" Acme ")
        assert head.calls == [
            "https://github.com/acme",
            "https://registry.npmjs.org/acme",
            "https://pypi.org/pypi/acme/json",
        ]
        assert results == [
            HandleResult("github", "acme", HandleStatus.NOT_FOUND, "https://github.com/acme"),
            HandleResult("npm", "acme", HandleStatus.TAKEN, "https://www.npmjs.com/package/acme"),
            HandleResult("pypi", "acme", HandleStatus.NOT_FOUND, "https://pypi.org/project/acme/"),
        ]

    @pytest.mark.parametrize(
        ("answer", "detail"),
        [
            (429, "the service answered 429"),
            (301, "the service answered 301"),
            (httpx.ConnectTimeout("timed out"), "timed out"),
            (httpx.ConnectError(""), "ConnectError"),
        ],
    )
    def test_anything_else_is_unknown_never_guessed(
        self, answer: int | Exception, detail: str
    ) -> None:
        head = FakeHead({"https://github.com/acme": answer})
        github, npm, _ = HttpHandleChecker(head=head).check("acme")
        assert (github.status, github.detail) == (HandleStatus.UNKNOWN, detail)
        assert github.url == "https://github.com/acme"
        assert npm.status is HandleStatus.NOT_FOUND  # one failure does not stop the others

    @pytest.mark.parametrize(
        ("name", "invalid"),
        [
            ("ñandú", {"github", "npm", "pypi"}),
            ("a" * 40, {"github"}),
            ("my--app", {"github"}),
            ("my-app", set()),
            ("9lives", set()),
        ],
    )
    def test_names_a_service_would_refuse_are_not_asked(self, name: str, invalid: set[str]) -> None:
        head = FakeHead({})
        results = HttpHandleChecker(head=head).check(name)
        assert {r.service for r in results if r.status is HandleStatus.INVALID} == invalid
        assert len(head.calls) == len(SERVICES) - len(invalid)
        assert all(r.detail and not r.url for r in results if r.status is HandleStatus.INVALID)

    def test_the_real_request_identifies_the_tool_and_does_not_follow_redirects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def fake_head(url: str, **kwargs: Any) -> httpx.Response:
            seen.update(kwargs, url=url)
            return httpx.Response(200)

        monkeypatch.setattr(httpx, "head", fake_head)
        checker = HttpHandleChecker(contact="me@example.com", timeout=3.0)
        assert checker.check("acme")[0].status is HandleStatus.TAKEN
        assert seen["headers"]["User-Agent"].startswith("domainhack/")
        assert seen["headers"]["From"] == "me@example.com"
        assert (seen["timeout"], seen["follow_redirects"]) == (3.0, False)


class FakeHandles(HandleChecker):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def check(self, name: str) -> list[HandleResult]:
        self.calls.append(name)
        return [
            HandleResult("github", name, HandleStatus.TAKEN, f"https://github.com/{name}"),
            HandleResult(
                "npm", name, HandleStatus.NOT_FOUND, f"https://www.npmjs.com/package/{name}"
            ),
            HandleResult("pypi", name, HandleStatus.UNKNOWN, "", "the service answered 503"),
        ]


def _name(*argv: str, handles: HandleChecker | None) -> int:
    registrar = ScriptedRegistrar(default=Availability.AVAILABLE)
    services = NameServices(KNOWN, None, supported_tlds=lambda: (), handles=handles)
    args = ["name", *argv, "--no-progress", "--no-cache", "--no-subdomain-hacks"]
    return main(args, catalog=FakeCatalog(registrar), services=services)


class TestNameCommand:
    def test_not_asked_by_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        fake = FakeHandles()
        assert _name("acme", "--tlds", "com", handles=fake) == EXIT_OK
        assert fake.calls == []
        assert "Handles" not in capsys.readouterr().out
        _name("acme", "--tlds", "com", "--format", "json", handles=fake)
        assert json.loads(capsys.readouterr().out)["handles"] is None

    def test_text(self, capsys: pytest.CaptureFixture[str]) -> None:
        fake = FakeHandles()
        assert _name("Acme", "--tlds", "com", "--handles", handles=fake) == EXIT_OK
        assert fake.calls == ["acme"]
        out = capsys.readouterr().out
        section = out.split("Handles for 'acme'\n")[1].split("\n\n")[0]
        assert [" ".join(line.split()) for line in section.splitlines()] == [
            "github taken https://github.com/acme",
            "npm not found (looks free) https://www.npmjs.com/package/acme",
            "pypi could not check the service answered 503",
        ]
        assert out.index("Handles for") < out.index("Recommendation")

    def test_markdown(self, capsys: pytest.CaptureFixture[str]) -> None:
        argv = ("acme", "--tlds", "com", "--handles", "--format", "markdown")
        assert _name(*argv, handles=FakeHandles()) == EXIT_OK
        out = capsys.readouterr().out
        assert "## Handles for `acme`" in out
        assert "| github | taken | https://github.com/acme |" in out

    def test_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        argv = ("acme", "--tlds", "com", "--handles", "--format", "json")
        assert _name(*argv, handles=FakeHandles()) == EXIT_OK
        handles = json.loads(capsys.readouterr().out)["handles"]
        assert [(h["service"], h["status"]) for h in handles] == [
            ("github", "taken"),
            ("npm", "not-found"),
            ("pypi", "unknown"),
        ]
        assert handles[2]["detail"] == "the service answered 503"

    def test_handles_do_not_change_the_recommendation(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _name("acme", "--tlds", "com,io", "--format", "json", handles=FakeHandles())
        without = json.loads(capsys.readouterr().out)["recommendations"]
        _name("acme", "--tlds", "com,io", "--handles", "--format", "json", handles=FakeHandles())
        assert json.loads(capsys.readouterr().out)["recommendations"] == without

    def test_default_checker_gets_the_contact(self, monkeypatch: pytest.MonkeyPatch) -> None:
        built: list[str | None] = []

        class Recording(FakeHandles):
            def __init__(self, *, contact: str | None = None) -> None:
                super().__init__()
                built.append(contact)

        monkeypatch.setattr("domainhack.cli.name_cmd.HttpHandleChecker", Recording)
        argv = ("acme", "--tlds", "com", "--handles", "--contact", "me@example.com")
        assert _name(*argv, handles=None) == EXIT_OK
        assert built == ["me@example.com"]


def test_mcp_tool_takes_handles() -> None:
    fake = FakeHandles()
    server = build_server(
        run=run_options(["--no-cache"]),
        catalog=FakeCatalog(ScriptedRegistrar(default=Availability.AVAILABLE)),
        services=NameServices(KNOWN, None, supported_tlds=lambda: (), handles=fake),
    )

    async def call(**arguments: Any) -> Any:
        return await server.call_tool("find_domain_for_name", arguments)

    data = anyio.run(lambda: call(name="acme", tlds="com")).structured_content
    assert data["handles"] is None
    data = anyio.run(lambda: call(name="acme", tlds="com", handles=True)).structured_content
    assert [h["service"] for h in data["handles"]] == ["github", "npm", "pypi"]
    assert fake.calls == ["acme"]
