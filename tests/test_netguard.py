"""The unit-test network guard (conftest ``network_guard``) really blocks the network."""

import socket
import subprocess
import sys

import httpx
import pytest

from domainhack.adapters.rdap_registrar import RdapRegistrarClient
from domainhack.adapters.whois_registrar import WhoisRegistrarClient
from tests.fakes import guarded_env, hack, run_cli
from tests.netguard import NetworkBlockedError, NetworkGuard

# TEST-NET-1 (RFC 5737): never routable, so nothing leaks even if the guard failed.
UNROUTABLE = ("192.0.2.1", 80)


@pytest.fixture
def guard(network_guard: NetworkGuard | None) -> NetworkGuard:
    assert network_guard is not None
    return network_guard


def _expect_blocked(guard: NetworkGuard) -> None:
    """Called after a deliberate attempt: check it was recorded, then forget it."""
    assert guard.attempts
    guard.attempts.clear()


def test_create_connection_is_blocked(guard: NetworkGuard) -> None:
    with pytest.raises(NetworkBlockedError, match="create_connection"):
        socket.create_connection(UNROUTABLE, timeout=0.1)
    _expect_blocked(guard)


def test_getaddrinfo_is_blocked(guard: NetworkGuard) -> None:
    with pytest.raises(NetworkBlockedError, match="getaddrinfo"):
        socket.getaddrinfo("example.com", 443)
    _expect_blocked(guard)


@pytest.mark.parametrize("family", [socket.AF_INET, socket.AF_INET6])
def test_inet_socket_connect_is_blocked(guard: NetworkGuard, family: socket.AddressFamily) -> None:
    address = UNROUTABLE if family == socket.AF_INET else ("2001:db8::1", 80, 0, 0)
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        with pytest.raises(NetworkBlockedError, match="connect"):
            sock.connect(address)
        with pytest.raises(NetworkBlockedError, match="connect_ex"):
            sock.connect_ex(address)
    _expect_blocked(guard)


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs Unix sockets")
def test_unix_sockets_are_allowed(guard: NetworkGuard) -> None:
    left, right = socket.socketpair()
    with left, right:
        left.sendall(b"ok")
        assert right.recv(2) == b"ok"
    # A real (failed) connect, not the guard error.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock, pytest.raises(OSError):
        sock.connect("/nonexistent/domainhack-test.sock")
    assert guard.attempts == []


def test_real_rdap_client_cannot_reach_the_network(guard: NetworkGuard) -> None:
    """The guard error is not an httpx/OSError, so adapters cannot turn it into ERROR."""
    with (
        RdapRegistrarClient("https://rdap.example.test/", delay=0.0) as client,
        pytest.raises(NetworkBlockedError),
    ):
        client.check_availability(hack("pla", "io"))
    _expect_blocked(guard)


def test_real_whois_client_cannot_reach_the_network(guard: NetworkGuard) -> None:
    client = WhoisRegistrarClient(delay=0.0)
    with pytest.raises(NetworkBlockedError):
        client.check_availability(hack("pla", "it"))
    _expect_blocked(guard)


def test_httpx_is_blocked(guard: NetworkGuard) -> None:
    with pytest.raises(NetworkBlockedError):
        httpx.get("https://example.com/", timeout=0.1)
    _expect_blocked(guard)


def test_uninstall_restores_socket_functions() -> None:
    originals = (socket.create_connection, socket.getaddrinfo, socket.socket.connect)
    inner = NetworkGuard()
    inner.install()
    inner.uninstall()
    assert (socket.create_connection, socket.getaddrinfo, socket.socket.connect) == originals


def test_cli_subprocesses_are_guarded_too() -> None:
    # run_cli's sitecustomize installs the same guard in the child interpreter,
    # without breaking the CLI itself.
    assert run_cli("--version").returncode == 0
    code = "import socket; socket.create_connection(('192.0.2.1', 80), timeout=0.1)"
    probe = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        env=guarded_env(),
    )
    assert probe.returncode != 0
    assert "NetworkBlockedError: network access blocked" in probe.stderr


@pytest.mark.integration
def test_integration_tests_are_exempt(network_guard: NetworkGuard | None) -> None:
    # Runs only with ``-m integration``; it opens no connection itself.
    assert network_guard is None
    assert socket.create_connection.__module__ == "socket"
