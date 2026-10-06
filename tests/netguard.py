"""Block real network access in unit tests.

``NetworkGuard.install()`` replaces ``socket.socket.connect``/``connect_ex``
(for AF_INET/AF_INET6 only, so Unix sockets keep working),
``socket.create_connection`` and ``socket.getaddrinfo`` with versions that
record the attempt and raise ``NetworkBlockedError``.

The error is a ``RuntimeError``, not an ``OSError``: adapters turn OSError and
httpx transport errors into ERROR results, which would hide the attempt. The
autouse fixture in ``conftest.py`` also fails the test at teardown if any
attempt was recorded, in case some code swallowed the error anyway.

``tests/netguard_site/sitecustomize.py`` installs the same guard in CLI
subprocesses (see ``tests.fakes.run_cli``).
"""

from __future__ import annotations

import socket
from typing import Any, NoReturn

_INET_FAMILIES = (socket.AF_INET, socket.AF_INET6)


class NetworkBlockedError(RuntimeError):
    """A unit test tried to reach the network (mark it ``integration`` if intended)."""


class NetworkGuard:
    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._saved: dict[tuple[object, str], Any] = {}

    def _block(self, what: str) -> NoReturn:
        self.attempts.append(what)
        raise NetworkBlockedError(
            f"network access blocked in unit tests: {what} "
            "(use a fake, or mark the test @pytest.mark.integration)"
        )

    def _replace(self, owner: object, name: str, value: object) -> None:
        self._saved[(owner, name)] = getattr(owner, name)
        setattr(owner, name, value)

    def install(self) -> None:
        if self._saved:
            return
        real_connect = socket.socket.connect
        real_connect_ex = socket.socket.connect_ex

        def connect(sock: socket.socket, address: Any) -> None:
            if sock.family in _INET_FAMILIES:
                self._block(f"connect({address!r})")
            real_connect(sock, address)

        def connect_ex(sock: socket.socket, address: Any) -> int:
            if sock.family in _INET_FAMILIES:
                self._block(f"connect_ex({address!r})")
            return real_connect_ex(sock, address)

        def create_connection(address: Any, *args: Any, **kwargs: Any) -> NoReturn:
            self._block(f"create_connection({address!r})")

        def getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> NoReturn:
            self._block(f"getaddrinfo({host!r}, {port!r})")

        self._replace(socket.socket, "connect", connect)
        self._replace(socket.socket, "connect_ex", connect_ex)
        self._replace(socket, "create_connection", create_connection)
        self._replace(socket, "getaddrinfo", getaddrinfo)

    def uninstall(self) -> None:
        for (owner, name), original in self._saved.items():
            setattr(owner, name, original)
        self._saved.clear()
