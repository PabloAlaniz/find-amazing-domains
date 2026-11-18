"""Is a name taken on GitHub, npm and PyPI? One HEAD request per service.

Each service answers 200 for a name that exists and 404 for one that does
not, on a public page that needs no account:

- GitHub: ``https://github.com/<name>`` (a user or an organization);
- npm: ``https://registry.npmjs.org/<name>`` (an unscoped package);
- PyPI: ``https://pypi.org/pypi/<name>/json`` (a project).

"Not found" is not a promise: services reserve names, and npm and PyPI
refuse names too similar to existing ones. Anything but 200 or 404 (a rate
limit, a timeout) is reported as unknown, never guessed.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from domainhack.adapters._http import identity_headers
from domainhack.ports.handle_checker import HandleChecker, HandleResult, HandleStatus

DEFAULT_HANDLE_TIMEOUT = 10.0


@dataclass(frozen=True)
class HandleService:
    """A service, the names it accepts, the URL that answers and the page to show."""

    name: str
    valid: re.Pattern[str]
    rule: str
    probe_url: str
    page_url: str


SERVICES: tuple[HandleService, ...] = (
    HandleService(
        "github",
        # Letters, digits and single inner hyphens, at most 39 characters.
        re.compile(r"(?!.*--)[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?"),
        "GitHub names are letters, digits and single hyphens, at most 39 characters",
        "https://github.com/{name}",
        "https://github.com/{name}",
    ),
    HandleService(
        "npm",
        re.compile(r"[a-z0-9][a-z0-9._-]{0,213}"),
        "npm package names are lowercase letters, digits, '.', '_' and '-', at most 214 characters",
        "https://registry.npmjs.org/{name}",
        "https://www.npmjs.com/package/{name}",
    ),
    HandleService(
        "pypi",
        re.compile(r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?"),
        "PyPI project names are letters, digits, '.', '_' and '-'",
        "https://pypi.org/pypi/{name}/json",
        "https://pypi.org/project/{name}/",
    ),
)

Head = Callable[[str], int]
"""Sends a HEAD request and returns the status code; raises ``httpx.HTTPError`` on failure."""


class HttpHandleChecker(HandleChecker):
    """``HandleChecker`` over HTTPS. ``head`` replaces the HTTP call (tests pass a fake)."""

    def __init__(
        self,
        *,
        contact: str | None = None,
        timeout: float = DEFAULT_HANDLE_TIMEOUT,
        head: Head | None = None,
    ) -> None:
        self._headers = identity_headers(contact)
        self._timeout = timeout
        self._head = head if head is not None else self._http_head

    def _http_head(self, url: str) -> int:
        response = httpx.head(
            url, headers=self._headers, timeout=self._timeout, follow_redirects=False
        )
        return response.status_code

    def check(self, name: str) -> list[HandleResult]:
        handle = name.strip().lower()
        return [self._check_one(service, handle) for service in SERVICES]

    def _check_one(self, service: HandleService, handle: str) -> HandleResult:
        if not (handle.isascii() and service.valid.fullmatch(handle)):
            return HandleResult(service.name, handle, HandleStatus.INVALID, detail=service.rule)
        page = service.page_url.format(name=handle)
        try:
            code = self._head(service.probe_url.format(name=handle))
        except httpx.HTTPError as exc:
            reason = str(exc) or type(exc).__name__
            return HandleResult(service.name, handle, HandleStatus.UNKNOWN, page, reason)
        if code == 200:
            return HandleResult(service.name, handle, HandleStatus.TAKEN, page)
        if code == 404:
            return HandleResult(service.name, handle, HandleStatus.NOT_FOUND, page)
        return HandleResult(
            service.name, handle, HandleStatus.UNKNOWN, page, f"the service answered {code}"
        )
