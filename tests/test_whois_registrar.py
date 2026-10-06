from collections.abc import Mapping

import pytest

from domainhack.adapters._circuit import HostCircuitBreaker
from domainhack.adapters.rdap_bootstrap import RDAP_OVERRIDES
from domainhack.adapters.whois_registrar import (
    ALL_WHOIS_SERVERS,
    DEFAULT_MIN_INTERVAL,
    WHOIS_FALLBACK_SERVERS,
    WHOIS_PORT,
    WHOIS_SERVERS,
    WhoisRegistrarClient,
    WhoisServer,
    _server,
    whois_query,
)
from domainhack.domain.entities import TLD, Availability, DomainHack
from tests.fakes import FakeClock, fake_throttle


class FakeConn:
    def __init__(self, reply: bytes, chunk: int = 7) -> None:
        self._reply = reply
        self._chunk = chunk
        self.sent = b""
        self.closed = False
        self.read_timeout: float | None = None

    def settimeout(self, value: float | None, /) -> None:
        self.read_timeout = value

    def sendall(self, data: bytes, /) -> None:
        self.sent += data

    def recv(self, bufsize: int, /) -> bytes:
        out, self._reply = self._reply[: self._chunk], self._reply[self._chunk :]
        return out

    def close(self) -> None:
        self.closed = True


class FakeConnector:
    def __init__(self, reply: bytes | Exception) -> None:
        self.reply = reply
        self.addresses: list[tuple[str, int]] = []
        self.timeouts: list[float] = []
        self.conns: list[FakeConn] = []

    def __call__(self, address: tuple[str, int], timeout: float) -> FakeConn:
        self.addresses.append(address)
        self.timeouts.append(timeout)
        if isinstance(self.reply, Exception):
            raise self.reply
        conn = FakeConn(self.reply)
        self.conns.append(conn)
        return conn


def _client(
    reply: bytes | Exception,
    delay: float = 0.0,
    clock: FakeClock | None = None,
    servers: Mapping[str, WhoisServer] = ALL_WHOIS_SERVERS,
) -> tuple[WhoisRegistrarClient, FakeConnector, FakeClock]:
    clock = clock or FakeClock()
    connector = FakeConnector(reply)
    client = WhoisRegistrarClient(
        delay=delay,
        timeout=5.0,
        servers=servers,
        connect=connector,
        throttle=fake_throttle(clock),
    )
    return client, connector, clock


def _hack(sld: str, tld: str) -> DomainHack:
    if tld == "it":
        sld = sld.ljust(3, "x")  # .it requires >= 3 characters
    return DomainHack.from_sld(sld, TLD(tld))


# One realistic "unregistered" reply per TLD in the primary and fallback tables.
NOT_FOUND_SAMPLES = {
    "it": "Domain:             zq.it\nStatus:             AVAILABLE\n",
    "am": "No match\n",
    "at": "% Copyright (c)2026 by NIC.AT\n% nothing found\n",
    "be": "Domain:\tzq.be\nStatus:\tAVAILABLE\n",
    "gg": "NOT FOUND\n",
    "im": "The domain zq.im was not found.\n",
    "la": "DOMAIN NOT FOUND\n",
    "ma": "No Object Found\n",
    "mx": "Object_Not_Found\n",
    "nu": 'domain "zq.nu" not found.\n',
    "pe": "Domain Status: No Object Found\n",
    "st": "No entries found for domain zq.st\n",
    "fm": "DOMAIN NOT FOUND\n",
    "re": "%% NOT FOUND\n",
    "tv": "No Data Found\n",
    "ly": "No Object Found\n",
    "is": "% No entries found for query 'zq.is'.\n",
    "in": "Domain zq.in is available for registration\n",
    "ar": "El dominio no se encuentra registrado en NIC Argentina\n",
}


def test_every_server_has_a_sample() -> None:
    assert set(NOT_FOUND_SAMPLES) == set(ALL_WHOIS_SERVERS)


def test_tables_are_disjoint_and_skip_rdap_overrides() -> None:
    # An RDAP override always wins, so a WHOIS entry for it could never be used.
    assert not set(WHOIS_SERVERS) & set(WHOIS_FALLBACK_SERVERS)
    assert not set(ALL_WHOIS_SERVERS) & set(RDAP_OVERRIDES)


@pytest.mark.parametrize("tld", sorted(NOT_FOUND_SAMPLES))
def test_not_found_pattern_means_available(tld: str) -> None:
    client, connector, _ = _client(NOT_FOUND_SAMPLES[tld].encode())
    result = client.check_availability(_hack("zq", tld))
    assert result.availability == Availability.AVAILABLE, tld
    assert connector.addresses == [(ALL_WHOIS_SERVERS[tld].host, WHOIS_PORT)]
    assert result.raw_title == f"whois {ALL_WHOIS_SERVERS[tld].host}"


def test_registered_reply_is_taken() -> None:
    reply = b"Domain:             google.it\nStatus:             ok\nCreated: 1999-12-10\n"
    client, _, _ = _client(reply)
    assert client.check_availability(_hack("google", "it")).availability == Availability.TAKEN


def test_uppercase_not_found_in_prose_does_not_match_lowercase_pattern() -> None:
    # gg pattern is case-sensitive "NOT FOUND"; a disclaimer saying "not found" is fine.
    reply = b"Domain:\n     google.gg\nIf a record is not found, contact us.\n"
    client, _, _ = _client(reply)
    assert client.check_availability(_hack("google", "gg")).availability == Availability.TAKEN


def test_sends_fqdn_with_crlf_and_closes() -> None:
    client, connector, _ = _client(b"No match\n")
    client.check_availability(_hack("pla", "am"))
    assert connector.conns[0].sent == b"pla.am\r\n"
    assert connector.conns[0].closed
    assert connector.timeouts == [5.0]
    assert connector.conns[0].read_timeout == 5.0


def test_taken_pattern_is_required_when_set() -> None:
    # DENIC-style server: "Status: connect" is the only TAKEN answer.
    servers = {"de": _server("whois.denic.de", r"Status:\s*free", taken=r"Status:\s*connect")}
    client, _, _ = _client(b"Domain: google.de\nStatus: connect\n", servers=servers)
    assert client.check_availability(_hack("google", "de")).availability == Availability.TAKEN
    client, _, _ = _client(b"Something unexpected\n", servers=servers)
    assert client.check_availability(_hack("google", "de")).availability == Availability.ERROR


@pytest.mark.parametrize("reply", [b"", b"   \r\n"])
def test_empty_reply_is_error(reply: bytes) -> None:
    client, _, _ = _client(reply)
    result = client.check_availability(_hack("pla", "it"))
    assert result.availability == Availability.ERROR
    assert "Empty" in result.error_message


@pytest.mark.parametrize(
    "reply",
    [
        b"Query rate limit exceeded. Try again later.\n",
        b"Requests of this client are not permitted\n",
        b"Too many queries from your IP\n",
    ],
)
def test_rate_limit_text_is_error(reply: bytes) -> None:
    client, _, _ = _client(reply)
    assert client.check_availability(_hack("pla", "it")).availability == Availability.ERROR


def test_throttling_boilerplate_in_a_record_is_still_taken() -> None:
    # Identity Digital / CentralNic append throttling notes to every reply.
    reply = (
        b"Domain Name: google.tv\r\nRegistrar: MarkMonitor\r\n"
        b"Queries to the Whois services are throttled. If too many queries are received...\r\n"
        b"Access to the whois service is rate limited.\r\n"
    )
    client, _, _ = _client(reply)
    assert client.check_availability(_hack("google", "tv")).availability == Availability.TAKEN


@pytest.mark.parametrize("exc", [TimeoutError("timed out"), ConnectionRefusedError("refused")])
def test_socket_errors_are_error(exc: Exception) -> None:
    client, _, _ = _client(exc)
    result = client.check_availability(_hack("pla", "it"))
    assert result.availability == Availability.ERROR
    assert "WHOIS query failed" in result.error_message


def test_unknown_tld_is_error_without_connecting() -> None:
    client, connector, _ = _client(b"x")
    result = client.check_availability(_hack("pla", "zz"))
    assert result.availability == Availability.ERROR
    assert connector.addresses == []
    assert client.supports("it")
    assert not client.supports("zz")


def test_delay_per_host() -> None:
    clock = FakeClock()
    client, _, _ = _client(b"No match\n", delay=1.5, clock=clock)
    client.check_availability(_hack("a", "am"))
    assert clock.sleeps == []
    client.check_availability(_hack("b", "am"))
    assert clock.sleeps == [1.5]
    client.check_availability(_hack("c", "it"))  # different host: no wait
    assert clock.sleeps == [1.5]


def test_whois_query_decodes_latin1_fallback() -> None:
    conn = FakeConn("Dueño: José\n".encode("latin-1"))
    text = whois_query("h", "q\r\n", 1.0, lambda addr, t: conn)
    assert "José" in text


def _timeout_client(timeout: float, **kwargs: float) -> tuple[WhoisRegistrarClient, FakeConnector]:
    clock = FakeClock()
    connector = FakeConnector(b"No match\n")
    client = WhoisRegistrarClient(
        delay=0.0,
        timeout=timeout,
        connect=connector,
        throttle=fake_throttle(clock),
        **kwargs,
    )
    return client, connector


def test_connect_timeout_is_shorter_than_read_timeout() -> None:
    client, connector = _timeout_client(10.0, connect_timeout=3.0)
    client.check_availability(_hack("pla", "am"))
    assert connector.timeouts == [3.0]
    assert connector.conns[0].read_timeout == 10.0


def test_connect_timeout_never_exceeds_read_timeout() -> None:
    client, connector = _timeout_client(2.0)
    client.check_availability(_hack("pla", "am"))
    assert connector.timeouts == [2.0]


def test_every_server_has_a_min_interval() -> None:
    assert all(s.min_interval >= DEFAULT_MIN_INTERVAL for s in WHOIS_SERVERS.values())
    assert WHOIS_SERVERS["it"].min_interval >= 4.0


def test_server_min_interval_overrides_a_smaller_delay() -> None:
    clock = FakeClock()
    client, _, _ = _client(b"Status: AVAILABLE\n", delay=1.0, clock=clock)
    for sld in ("a", "b", "c"):
        client.check_availability(_hack(sld, "it"))
    assert clock.sleeps == [WHOIS_SERVERS["it"].min_interval] * 2


def test_delay_larger_than_min_interval_wins() -> None:
    clock = FakeClock()
    client, _, _ = _client(b"Status: AVAILABLE\n", delay=7.0, clock=clock)
    client.check_availability(_hack("a", "it"))
    client.check_availability(_hack("b", "it"))
    assert clock.sleeps == [7.0]


def _breaker_client(
    reply: bytes | Exception, warnings: list[str]
) -> tuple[WhoisRegistrarClient, FakeConnector, FakeClock]:
    clock = FakeClock()
    connector = FakeConnector(reply)
    client = WhoisRegistrarClient(
        delay=0.0,
        timeout=5.0,
        connect=connector,
        throttle=fake_throttle(clock),
        breaker=HostCircuitBreaker(threshold=3, clock=clock.time, on_open=warnings.append),
    )
    return client, connector, clock


_TIMEOUT = TimeoutError("timed out")
_SLOW_DOWN_REPLIES = (_TIMEOUT, b"Too many queries\n")


@pytest.mark.parametrize(
    "reply",
    [_TIMEOUT, ConnectionResetError("reset"), b"", b"Too many queries\n"],
)
def test_breaker_skips_host_after_three_failures(reply: bytes | Exception) -> None:
    warnings: list[str] = []
    client, connector, _ = _breaker_client(reply, warnings)
    for sld in ("a", "b", "c"):
        assert client.check_availability(_hack(sld, "it")).availability == Availability.ERROR
    # Timeouts and rate-limit text are retried once; still one failure per check.
    queries = 6 if reply in _SLOW_DOWN_REPLIES else 3
    assert len(connector.addresses) == queries
    assert len(warnings) == 1

    skipped = [client.check_availability(_hack(sld, "it")) for sld in ("d", "e", "f")]
    assert len(connector.addresses) == queries  # no network call while open
    for result in skipped:
        assert result.availability == Availability.ERROR
        assert result.error_message == "skipped: whois.nic.it unresponsive (circuit open)"
        assert result.raw_title == "whois whois.nic.it"
    assert len(warnings) == 1


def test_open_circuit_does_not_wait_on_the_throttle() -> None:
    client, _, clock = _breaker_client(TimeoutError("timed out"), [])
    for sld in ("a", "b", "c"):
        client.check_availability(_hack(sld, "it"))
    sleeps = list(clock.sleeps)
    client.check_availability(_hack("d", "it"))
    assert clock.sleeps == sleeps


def test_breaker_is_per_host() -> None:
    client, connector, _ = _breaker_client(TimeoutError("timed out"), [])
    for sld in ("a", "b", "c"):
        client.check_availability(_hack(sld, "it"))
    client.check_availability(_hack("a", "am"))
    assert connector.addresses[-1] == ("whois.amnic.net", WHOIS_PORT)


def test_breaker_half_open_recovers_after_cooldown() -> None:
    client, connector, clock = _breaker_client(TimeoutError("timed out"), [])
    for sld in ("a", "b", "c"):
        client.check_availability(_hack(sld, "it"))
    connector.reply = b"Status: AVAILABLE\n"
    assert "circuit open" in client.check_availability(_hack("d", "it")).error_message
    clock.now += 60.0
    assert client.check_availability(_hack("e", "it")).availability == Availability.AVAILABLE
    assert client.check_availability(_hack("f", "it")).availability == Availability.AVAILABLE


def test_real_answers_reset_the_failure_count() -> None:
    warnings: list[str] = []
    client, connector, _ = _breaker_client(TimeoutError("timed out"), warnings)
    for i in range(5):
        connector.reply = TimeoutError("timed out")
        client.check_availability(_hack(f"a{i}", "it"))
        client.check_availability(_hack(f"b{i}", "it"))
        connector.reply = b"Domain: x.it\nStatus: ok\n"
        result = client.check_availability(_hack(f"c{i}", "it"))
        assert result.availability == Availability.TAKEN
    assert warnings == []
