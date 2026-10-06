from collections.abc import Callable

import httpx
import pytest

from domainhack.adapters._circuit import HostCircuitBreaker
from domainhack.adapters._throttle import HostThrottle
from domainhack.adapters.rdap_registrar import RdapRegistrarClient, parse_retry_after
from domainhack.domain.entities import Availability
from tests.fakes import FakeClock, FakeRandom, fake_throttle, hack

BASE = "https://rdap.example.test/rdap/"
Handler = Callable[[httpx.Request], httpx.Response]


def _client(
    handler: Handler,
    delay: float = 0.0,
    clock: FakeClock | None = None,
    throttle: HostThrottle | None = None,
    random: FakeRandom | None = None,
    **kwargs: object,
) -> tuple[RdapRegistrarClient, FakeClock]:
    clock = clock or FakeClock()
    throttle = throttle or fake_throttle(clock)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RdapRegistrarClient(
        BASE,
        delay=delay,
        client=http,
        throttle=throttle,
        random=random or FakeRandom(),
        **kwargs,  # type: ignore[arg-type]
    )
    return client, clock


class TestRdapVerdicts:
    def test_404_is_available(self) -> None:
        client, _ = _client(lambda r: httpx.Response(404, json={"errorCode": 404}))
        result = client.check_availability(hack(tld="io"))
        assert result.availability == Availability.AVAILABLE
        assert result.raw_title == "HTTP 404"

    def test_html_404_is_still_available(self) -> None:
        # rdap.nic.ar answers 404 with text/html; the status code decides.
        client, _ = _client(lambda r: httpx.Response(404, html="<h1>Not found</h1>"))
        assert client.check_availability(hack(tld="io")).availability == Availability.AVAILABLE

    def test_200_matching_ldh_name_is_taken(self) -> None:
        body = {"objectClassName": "domain", "ldhName": "PLA.IO"}
        client, _ = _client(lambda r: httpx.Response(200, json=body))
        result = client.check_availability(hack(tld="io"))
        assert result.availability == Availability.TAKEN
        assert result.raw_title == "HTTP 200"

    def test_200_trailing_dot_ldh_name_is_taken(self) -> None:
        client, _ = _client(lambda r: httpx.Response(200, json={"ldhName": "pla.io."}))
        assert client.check_availability(hack(tld="io")).availability == Availability.TAKEN

    def test_200_domain_object_without_ldh_name_is_taken(self) -> None:
        client, _ = _client(lambda r: httpx.Response(200, json={"objectClassName": "domain"}))
        assert client.check_availability(hack(tld="io")).availability == Availability.TAKEN

    def test_200_mismatched_ldh_name_is_error(self) -> None:
        client, _ = _client(lambda r: httpx.Response(200, json={"ldhName": "other.io"}))
        result = client.check_availability(hack(tld="io"))
        assert result.availability == Availability.ERROR
        assert "mismatch" in result.error_message

    def test_200_html_page_is_error(self) -> None:
        # rdap.gg returns an HTML page with 200 for every name.
        client, _ = _client(lambda r: httpx.Response(200, html="<html>hello</html>"))
        result = client.check_availability(hack(tld="io"))
        assert result.availability == Availability.ERROR
        assert "not JSON" in result.error_message

    def test_200_non_object_json_is_error(self) -> None:
        client, _ = _client(lambda r: httpx.Response(200, json=["x"]))
        assert client.check_availability(hack(tld="io")).availability == Availability.ERROR

    def test_200_json_without_domain_markers_is_error(self) -> None:
        client, _ = _client(lambda r: httpx.Response(200, json={"foo": "bar"}))
        assert client.check_availability(hack(tld="io")).availability == Availability.ERROR

    @pytest.mark.parametrize("status", [400, 403, 500, 502, 503])
    def test_other_statuses_are_error(self, status: int) -> None:
        client, _ = _client(lambda r: httpx.Response(status))
        result = client.check_availability(hack(tld="io"))
        assert result.availability == Availability.ERROR
        assert result.raw_title == f"HTTP {status}"

    def test_timeout_is_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        client, _ = _client(handler)
        result = client.check_availability(hack(tld="io"))
        assert result.availability == Availability.ERROR
        assert "timed out" in result.error_message

    def test_connect_error_is_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client, _ = _client(handler)
        assert client.check_availability(hack(tld="io")).availability == Availability.ERROR


class TestRdapRequest:
    def test_requests_domain_path_with_rdap_accept(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(404)

        client, _ = _client(handler)
        client.check_availability(hack("goo", "gl"))
        assert str(seen[0].url) == f"{BASE}domain/goo.gl"
        assert "application/rdap+json" in seen[0].headers["Accept"]

    def test_base_url_gets_trailing_slash(self) -> None:
        client = RdapRegistrarClient("https://rdap.example.test", delay=0.0)
        assert client.base_url == "https://rdap.example.test/"
        client.close()


class TestRetryAfter:
    def test_429_with_short_retry_after_is_retried(self) -> None:
        responses = iter(
            [
                httpx.Response(429, headers={"Retry-After": "3"}),
                httpx.Response(200, json={"ldhName": "pla.io"}),
            ]
        )
        client, clock = _client(lambda r: next(responses))
        result = client.check_availability(hack(tld="io"))
        assert result.availability == Availability.TAKEN
        assert 3.0 in clock.sleeps

    def test_429_retries_are_bounded(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(429, headers={"Retry-After": "1"})

        client, _ = _client(handler, max_retries=2)
        result = client.check_availability(hack(tld="io"))
        assert result.availability == Availability.ERROR
        assert result.raw_title == "HTTP 429"
        assert len(calls) == 3

    def test_429_without_retry_after_is_retried_then_error(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(429)

        client, _ = _client(handler)
        result = client.check_availability(hack(tld="io"))
        assert result.availability == Availability.ERROR
        assert result.error_message == "RDAP rate limited (429)"
        assert len(calls) == 3

    def test_429_with_long_retry_after_is_error_immediately(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(429, headers={"Retry-After": "3600"})

        client, clock = _client(handler)
        assert client.check_availability(hack(tld="io")).availability == Availability.ERROR
        assert len(calls) == 1
        assert clock.sleeps == []

    def test_parse_retry_after(self) -> None:
        assert parse_retry_after("5") == 5.0
        assert parse_retry_after(None) is None
        assert parse_retry_after("soon") is None
        assert parse_retry_after("Thu, 01 Jan 1970 00:01:00 GMT", now=0.0) == 60.0
        assert parse_retry_after("Thu, 01 Jan 1970 00:00:00 GMT", now=100.0) == 0.0


class TestRdapDelay:
    def test_no_sleep_on_first_request_then_delay(self) -> None:
        client, clock = _client(lambda r: httpx.Response(404), delay=0.5)
        client.check_availability(hack(tld="io"))
        assert clock.sleeps == []
        client.check_availability(hack(tld="io"))
        assert clock.sleeps == [0.5]

    def test_delay_is_shared_per_host_across_instances(self) -> None:
        clock = FakeClock()
        shared = fake_throttle(clock)
        a, _ = _client(lambda r: httpx.Response(404), delay=1.0, clock=clock, throttle=shared)
        b, _ = _client(lambda r: httpx.Response(404), delay=1.0, clock=clock, throttle=shared)
        a.check_availability(hack(tld="io"))
        b.check_availability(hack(tld="io"))
        assert clock.sleeps == [1.0]


class TestRdapLifecycle:
    def test_close_closes_owned_client(self) -> None:
        client = RdapRegistrarClient(BASE, delay=0.0)
        with client:
            pass
        assert client.http_client.is_closed

    def test_close_leaves_injected_client_open(self) -> None:
        http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
        with RdapRegistrarClient(BASE, delay=0.0, client=http):
            pass
        assert not http.is_closed
        http.close()

    def test_owned_client_has_a_short_connect_timeout(self) -> None:
        client = RdapRegistrarClient(BASE, delay=0.0, timeout=10.0)
        assert client.http_client.timeout.connect == 5.0
        assert client.http_client.timeout.read == 10.0
        client.close()


class TestRdapCircuitBreaker:
    @staticmethod
    def _counting(response: Callable[[httpx.Request], httpx.Response]) -> tuple[Handler, list[str]]:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return response(request)

        return handler, calls

    def _breaker_client(
        self, handler: Handler, warnings: list[str]
    ) -> tuple[RdapRegistrarClient, FakeClock]:
        clock = FakeClock()
        breaker = HostCircuitBreaker(threshold=3, clock=clock.time, on_open=warnings.append)
        return _client(handler, clock=clock, breaker=breaker)

    @staticmethod
    def _timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    @pytest.mark.parametrize(
        "response",
        [
            _timeout,
            lambda r: httpx.Response(429),
            lambda r: httpx.Response(503),
        ],
    )
    def test_opens_after_three_failures_and_skips_without_network(
        self, response: Callable[[httpx.Request], httpx.Response]
    ) -> None:
        handler, calls = self._counting(response)
        warnings: list[str] = []
        client, _ = self._breaker_client(handler, warnings)
        for sld in ("a", "b", "c"):
            assert client.check_availability(hack(sld, "io")).availability == Availability.ERROR
        result = client.check_availability(hack("d", "io"))
        assert len(calls) == 9  # 3 checks x (1 try + 2 retries); one failure per check
        assert result.availability == Availability.ERROR
        assert result.error_message == "skipped: rdap.example.test unresponsive (circuit open)"
        assert len(warnings) == 1

    def test_non_failure_errors_do_not_count(self) -> None:
        handler, calls = self._counting(lambda r: httpx.Response(400))
        warnings: list[str] = []
        client, _ = self._breaker_client(handler, warnings)
        for sld in ("a", "b", "c", "d"):
            client.check_availability(hack(sld, "io"))
        assert len(calls) == 4
        assert warnings == []

    def test_half_open_trial_recovers(self) -> None:
        state = {"fail": True}

        def handler(request: httpx.Request) -> httpx.Response:
            if state["fail"]:
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(404)

        client, clock = self._breaker_client(handler, [])
        for sld in ("a", "b", "c"):
            client.check_availability(hack(sld, "io"))
        state["fail"] = False
        assert client.check_availability(hack("d", "io")).availability == Availability.ERROR
        clock.now += 60.0
        assert client.check_availability(hack("e", "io")).availability == Availability.AVAILABLE
        assert client.check_availability(hack("f", "io")).availability == Availability.AVAILABLE

    def test_clients_sharing_a_breaker_share_the_circuit(self) -> None:
        clock = FakeClock()
        warnings: list[str] = []
        breaker = HostCircuitBreaker(threshold=3, clock=clock.time, on_open=warnings.append)
        handler, calls = self._counting(self._timeout)
        a, _ = _client(handler, clock=clock, breaker=breaker)
        b, _ = _client(handler, clock=clock, breaker=breaker)
        for sld in ("a", "b", "c"):
            a.check_availability(hack(sld, "io"))
        assert "circuit open" in b.check_availability(hack("x", "sh")).error_message
        assert len(calls) == 9


def _scripted(*responses: httpx.Response | type[Exception]) -> tuple[Handler, list[int]]:
    """A handler answering ``responses`` in turn (the last one repeats)."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        item = responses[min(len(calls), len(responses) - 1)]
        calls.append(1)
        if isinstance(item, httpx.Response):
            return item
        raise item("boom", request=request)  # type: ignore[call-arg]

    return handler, calls


HOST = "rdap.example.test"


class TestRetryBackoff:
    @pytest.mark.parametrize(
        ("value", "sleeps"),
        [
            (1.0, [2.0, 4.0]),  # full jitter at its top: 2 * 2**n
            (0.5, [1.0, 2.0]),
            # A zero backoff still waits the host's slowed-down interval (1 s):
            # the two waits overlap instead of adding up.
            (0.0, [1.0, 1.0]),
        ],
    )
    def test_429_without_retry_after_backs_off_with_full_jitter(
        self, value: float, sleeps: list[float]
    ) -> None:
        handler, calls = _scripted(httpx.Response(429))
        client, clock = _client(handler, random=FakeRandom(value))
        result = client.check_availability(hack(tld="io"))
        assert result.availability == Availability.ERROR
        assert len(calls) == 3
        assert clock.sleeps == sleeps

    def test_retry_after_is_honored_instead_of_backoff(self) -> None:
        handler, _ = _scripted(
            httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(404)
        )
        random = FakeRandom()
        client, clock = _client(handler, random=random)
        assert client.check_availability(hack(tld="io")).availability == Availability.AVAILABLE
        assert clock.sleeps == [7.0]
        assert random.calls == 0  # no backoff drawn

    def test_long_retry_after_defers_the_host_for_the_next_check(self) -> None:
        handler, calls = _scripted(
            httpx.Response(429, headers={"Retry-After": "3600"}), httpx.Response(404)
        )
        client, clock = _client(handler)
        assert client.check_availability(hack("a", "io")).availability == Availability.ERROR
        assert len(calls) == 1
        assert client.check_availability(hack("b", "io")).availability == Availability.AVAILABLE
        assert clock.sleeps == [60.0]  # Retry-After, capped at the throttle's max interval

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_5xx_is_retried_then_succeeds(self, status: int) -> None:
        taken = httpx.Response(200, json={"ldhName": "pla.io"})
        handler, calls = _scripted(httpx.Response(status), taken)
        client, _ = _client(handler)
        assert client.check_availability(hack(tld="io")).availability == Availability.TAKEN
        assert len(calls) == 2

    def test_final_5xx_keeps_its_status(self) -> None:
        handler, calls = _scripted(httpx.Response(502))
        client, _ = _client(handler)
        result = client.check_availability(hack(tld="io"))
        assert result.raw_title == "HTTP 502"
        assert len(calls) == 3

    @pytest.mark.parametrize(
        "error", [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError, httpx.ReadError]
    )
    def test_timeouts_and_dropped_connections_are_retried(
        self, error: type[httpx.HTTPError]
    ) -> None:
        handler, calls = _scripted(error, error, httpx.Response(404))
        client, _ = _client(handler)
        assert client.check_availability(hack(tld="io")).availability == Availability.AVAILABLE
        assert len(calls) == 3

    def test_other_transport_errors_are_not_retried(self) -> None:
        handler, calls = _scripted(httpx.UnsupportedProtocol)
        client, _ = _client(handler)
        assert client.check_availability(hack(tld="io")).availability == Availability.ERROR
        assert len(calls) == 1

    def test_max_retries_zero_disables_retries(self) -> None:
        handler, calls = _scripted(httpx.Response(503))
        client, clock = _client(handler, max_retries=0)
        assert client.check_availability(hack(tld="io")).availability == Availability.ERROR
        assert len(calls) == 1
        assert clock.sleeps == []


class TestAdaptiveThrottle:
    @pytest.mark.parametrize(
        "response",
        [httpx.Response(429), httpx.Response(503), httpx.ReadTimeout],
    )
    def test_slow_down_signals_slow_the_host_once_per_check(
        self, response: httpx.Response | type[Exception]
    ) -> None:
        handler, calls = _scripted(response)
        clock = FakeClock()
        throttle = fake_throttle(clock)
        client, _ = _client(handler, delay=1.0, clock=clock, throttle=throttle)
        client.check_availability(hack(tld="io"))
        assert len(calls) == 3
        assert throttle.interval(HOST) == 2.0  # doubled once, not 2**3

    def test_500_does_not_slow_the_host_down(self) -> None:
        handler, _ = _scripted(httpx.Response(500))
        clock = FakeClock()
        throttle = fake_throttle(clock)
        client, _ = _client(handler, delay=1.0, clock=clock, throttle=throttle)
        client.check_availability(hack(tld="io"))
        assert throttle.interval(HOST) == 1.0

    def test_answers_recover_the_interval(self) -> None:
        handler, _ = _scripted(httpx.Response(503), httpx.Response(404))
        clock = FakeClock()
        throttle = fake_throttle(clock)
        client, _ = _client(handler, delay=1.0, clock=clock, throttle=throttle)
        client.check_availability(hack("a", "io"))  # 503 then 404: slowed, then recovering
        assert throttle.interval(HOST) == pytest.approx(1.8)
        for sld in ("b", "c", "d", "e", "f", "g"):
            client.check_availability(hack(sld, "io"))
        assert throttle.interval(HOST) == 1.0


class TestBreakerComposition:
    @staticmethod
    def _setup(
        *responses: httpx.Response | type[Exception],
    ) -> tuple[RdapRegistrarClient, HostCircuitBreaker, list[int], list[str]]:
        handler, calls = _scripted(*responses)
        clock = FakeClock()
        warnings: list[str] = []
        breaker = HostCircuitBreaker(threshold=2, clock=clock.time, on_open=warnings.append)
        client, _ = _client(handler, clock=clock, breaker=breaker)
        return client, breaker, calls, warnings

    def test_a_check_with_retries_counts_as_one_failure(self) -> None:
        client, breaker, calls, warnings = self._setup(httpx.Response(503))
        client.check_availability(hack("a", "io"))
        assert len(calls) == 3
        assert not breaker.is_open(HOST)  # threshold 2: three attempts were one failure
        client.check_availability(hack("b", "io"))
        assert breaker.is_open(HOST)
        assert len(warnings) == 1

    def test_a_retry_that_succeeds_is_not_a_failure(self) -> None:
        client, breaker, _, _ = self._setup(
            httpx.ReadTimeout, httpx.Response(404), httpx.ReadTimeout, httpx.Response(404)
        )
        for sld in ("a", "b"):
            assert client.check_availability(hack(sld, "io")).availability == Availability.AVAILABLE
        assert not breaker.is_open(HOST)

    def test_open_circuit_skips_without_retrying(self) -> None:
        client, breaker, calls, _ = self._setup(httpx.Response(503))
        for sld in ("a", "b"):
            client.check_availability(hack(sld, "io"))
        assert breaker.is_open(HOST)
        made = len(calls)
        result = client.check_availability(hack("c", "io"))
        assert "circuit open" in result.error_message
        assert len(calls) == made
