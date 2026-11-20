"""WHOIS adaptive throttling: slow-down signals, one retry at most, breaker composition."""

import re
from dataclasses import dataclass

import pytest

from domainhack.adapters._circuit import HostCircuitBreaker
from domainhack.adapters._throttle import HostThrottle
from domainhack.adapters.whois_registrar import WHOIS_SERVERS, WhoisRegistrarClient, WhoisServer
from domainhack.domain.entities import TLD, Availability, DomainHack
from tests.fakes import FakeClock, FakeRandom, fake_throttle
from tests.test_whois_registrar import FakeConn

HOST = "whois.amnic.net"  # .am, paced at the 1 s floor
AVAILABLE = b"No match for pla.am\n"
TAKEN = b"Domain name: pla.am\nRegistrar: x\n"
RATE_LIMITED = b"Too many queries, try again later\n"

Reply = bytes | Exception


class SequencedConnector:
    """Answers ``replies`` in turn (the last one repeats)."""

    def __init__(self, *replies: Reply) -> None:
        self.replies = replies
        self.connects = 0

    def __call__(self, address: tuple[str, int], timeout: float) -> FakeConn:
        reply = self.replies[min(self.connects, len(self.replies) - 1)]
        self.connects += 1
        if isinstance(reply, Exception):
            raise reply
        return FakeConn(reply)


@dataclass
class Rig:
    client: WhoisRegistrarClient
    connector: SequencedConnector
    clock: FakeClock
    breaker: HostCircuitBreaker
    throttle: HostThrottle


def _rig(*replies: Reply, threshold: int = 3, **kwargs: int) -> Rig:
    clock = FakeClock()
    connector = SequencedConnector(*replies)
    breaker = HostCircuitBreaker(threshold=threshold, clock=clock.time, on_open=lambda _: None)
    throttle = fake_throttle(clock)
    client = WhoisRegistrarClient(
        delay=0.0,
        timeout=5.0,
        servers=WHOIS_SERVERS,
        connect=connector,
        throttle=throttle,
        breaker=breaker,
        random=FakeRandom(),
        **kwargs,
    )
    return Rig(client, connector, clock, breaker, throttle)


def _am(sld: str = "pla") -> DomainHack:
    return DomainHack.from_sld(sld, TLD("am"))


@pytest.mark.parametrize("first", [TimeoutError("timed out"), RATE_LIMITED])
def test_slow_down_signal_is_retried_once_after_slowing_down(first: Reply) -> None:
    rig = _rig(first, AVAILABLE)
    assert rig.client.check_availability(_am()).availability == Availability.AVAILABLE
    assert rig.connector.connects == 2
    # Interval 1 s doubled to 2 s; the 1 s backoff (0.5 * 2) overlaps with it.
    assert rig.clock.sleeps == [2.0]


@pytest.mark.parametrize("reply", [TimeoutError("timed out"), RATE_LIMITED])
def test_never_more_than_one_retry(reply: Reply) -> None:
    rig = _rig(reply, max_retries=5)
    assert rig.client.check_availability(_am()).availability == Availability.ERROR
    assert rig.connector.connects == 2


def test_retries_can_be_disabled() -> None:
    rig = _rig(TimeoutError("timed out"), max_retries=0)
    rig.client.check_availability(_am())
    assert rig.connector.connects == 1


@pytest.mark.parametrize("reply", [ConnectionResetError("reset"), b""])
def test_other_failures_are_not_retried_and_do_not_slow_down(reply: Reply) -> None:
    rig = _rig(reply)
    assert rig.client.check_availability(_am()).availability == Availability.ERROR
    assert rig.connector.connects == 1
    assert rig.throttle.interval(HOST) == 1.0


def test_slows_down_once_per_check_and_recovers_on_answers() -> None:
    rig = _rig(TimeoutError("timed out"), TimeoutError("timed out"), TAKEN)
    assert rig.client.check_availability(_am("a")).availability == Availability.ERROR
    assert rig.throttle.interval(HOST) == 2.0  # two timeouts in one check: doubled once
    assert rig.client.check_availability(_am("b")).availability == Availability.TAKEN
    assert rig.throttle.interval(HOST) == pytest.approx(1.8)


def test_a_check_counts_once_for_the_breaker() -> None:
    rig = _rig(TimeoutError("timed out"), threshold=2)
    rig.client.check_availability(_am("a"))
    assert rig.connector.connects == 2
    assert not rig.breaker.is_open(HOST)
    rig.client.check_availability(_am("b"))
    assert rig.breaker.is_open(HOST)
    result = rig.client.check_availability(_am("c"))
    assert "circuit open" in result.error_message
    assert rig.connector.connects == 4


def test_server_for() -> None:
    rig = _rig(AVAILABLE)
    server = rig.client.server_for("AM")
    assert server is not None and server.host == HOST
    assert rig.client.server_for("zz") is None


def test_a_refused_query_is_neutral_for_throttle_and_breaker() -> None:
    bad = WhoisServer(host=HOST, not_found=re.compile("No match"), query_format="x\n{fqdn}\r\n")
    connector = SequencedConnector(AVAILABLE)
    breaker = HostCircuitBreaker(threshold=1, on_open=lambda _: None)
    throttle = fake_throttle(FakeClock())
    client = WhoisRegistrarClient(
        servers={"am": bad}, connect=connector, throttle=throttle, breaker=breaker
    )
    result = client.check_availability(_am())
    assert "Refused to send WHOIS query" in result.error_message
    assert connector.connects == 0
    assert not breaker.is_open(HOST)
    assert throttle.interval(HOST) == 1.0


def test_half_open_trial_is_a_single_attempt() -> None:
    rig = _rig(TimeoutError("timed out"), threshold=1)
    rig.client.check_availability(_am("a"))
    assert rig.connector.connects == 2
    rig.clock.now += 60.0
    assert rig.client.check_availability(_am("b")).availability == Availability.ERROR
    assert rig.connector.connects == 3  # no retry for the trial


@pytest.mark.parametrize("reply", [TimeoutError("timed out"), RATE_LIMITED])
def test_no_retry_once_the_host_is_slowed_to_the_timeout(reply: Reply) -> None:
    rig = _rig(reply)
    while rig.throttle.slow_down(HOST) < 5.0:  # the rig's timeout
        pass
    assert rig.client.check_availability(_am()).availability == Availability.ERROR
    assert rig.connector.connects == 1


def test_gives_up_on_a_dead_host_for_the_run() -> None:
    rig = _rig(ConnectionResetError("reset"))
    for i in range(30):
        rig.client.check_availability(_am(f"d{i}"))
        rig.clock.now += 61.0
    assert rig.connector.connects == 3 + 3  # three checks, then three trials
    assert rig.breaker.dead_hosts() == (HOST,)
    result = rig.client.check_availability(_am("late"))
    assert result.error_message == "skipped: whois.amnic.net unresponsive (gave up for this run)"
