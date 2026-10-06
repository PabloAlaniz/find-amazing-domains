from collections.abc import Callable

import httpx
import pytest

from domainhack.adapters._circuit import HostCircuitBreaker
from domainhack.adapters._throttle import HostThrottle
from domainhack.adapters.rdap_registrar import RdapRegistrarClient, parse_retry_after
from domainhack.domain.entities import TLD, Availability, DomainHack

BASE = "https://rdap.example.test/rdap/"
Handler = Callable[[httpx.Request], httpx.Response]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _hack(sld: str = "pla", tld: str = "io") -> DomainHack:
    return DomainHack.from_sld(sld, TLD(tld))


def _client(
    handler: Handler,
    delay: float = 0.0,
    clock: FakeClock | None = None,
    throttle: HostThrottle | None = None,
    **kwargs: object,
) -> tuple[RdapRegistrarClient, FakeClock]:
    clock = clock or FakeClock()
    throttle = throttle or HostThrottle(clock=clock.time, sleep=clock.sleep)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RdapRegistrarClient(
        BASE,
        delay=delay,
        client=http,
        throttle=throttle,
        sleep=clock.sleep,
        **kwargs,  # type: ignore[arg-type]
    )
    return client, clock


class TestRdapVerdicts:
    def test_404_is_available(self) -> None:
        client, _ = _client(lambda r: httpx.Response(404, json={"errorCode": 404}))
        result = client.check_availability(_hack())
        assert result.availability == Availability.AVAILABLE
        assert result.raw_title == "HTTP 404"

    def test_html_404_is_still_available(self) -> None:
        # rdap.nic.ar answers 404 with text/html; the status code decides.
        client, _ = _client(lambda r: httpx.Response(404, html="<h1>Not found</h1>"))
        assert client.check_availability(_hack()).availability == Availability.AVAILABLE

    def test_200_matching_ldh_name_is_taken(self) -> None:
        body = {"objectClassName": "domain", "ldhName": "PLA.IO"}
        client, _ = _client(lambda r: httpx.Response(200, json=body))
        result = client.check_availability(_hack())
        assert result.availability == Availability.TAKEN
        assert result.raw_title == "HTTP 200"

    def test_200_trailing_dot_ldh_name_is_taken(self) -> None:
        client, _ = _client(lambda r: httpx.Response(200, json={"ldhName": "pla.io."}))
        assert client.check_availability(_hack()).availability == Availability.TAKEN

    def test_200_domain_object_without_ldh_name_is_taken(self) -> None:
        client, _ = _client(lambda r: httpx.Response(200, json={"objectClassName": "domain"}))
        assert client.check_availability(_hack()).availability == Availability.TAKEN

    def test_200_mismatched_ldh_name_is_error(self) -> None:
        client, _ = _client(lambda r: httpx.Response(200, json={"ldhName": "other.io"}))
        result = client.check_availability(_hack())
        assert result.availability == Availability.ERROR
        assert "mismatch" in result.error_message

    def test_200_html_page_is_error(self) -> None:
        # rdap.gg returns an HTML page with 200 for every name.
        client, _ = _client(lambda r: httpx.Response(200, html="<html>hello</html>"))
        result = client.check_availability(_hack())
        assert result.availability == Availability.ERROR
        assert "not JSON" in result.error_message

    def test_200_non_object_json_is_error(self) -> None:
        client, _ = _client(lambda r: httpx.Response(200, json=["x"]))
        assert client.check_availability(_hack()).availability == Availability.ERROR

    def test_200_json_without_domain_markers_is_error(self) -> None:
        client, _ = _client(lambda r: httpx.Response(200, json={"foo": "bar"}))
        assert client.check_availability(_hack()).availability == Availability.ERROR

    @pytest.mark.parametrize("status", [400, 403, 500, 502, 503])
    def test_other_statuses_are_error(self, status: int) -> None:
        client, _ = _client(lambda r: httpx.Response(status))
        result = client.check_availability(_hack())
        assert result.availability == Availability.ERROR
        assert result.raw_title == f"HTTP {status}"

    def test_timeout_is_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        client, _ = _client(handler)
        result = client.check_availability(_hack())
        assert result.availability == Availability.ERROR
        assert "timed out" in result.error_message

    def test_connect_error_is_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client, _ = _client(handler)
        assert client.check_availability(_hack()).availability == Availability.ERROR


class TestRdapRequest:
    def test_requests_domain_path_with_rdap_accept(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(404)

        client, _ = _client(handler)
        client.check_availability(_hack("goo", "gl"))
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
        result = client.check_availability(_hack())
        assert result.availability == Availability.TAKEN
        assert 3.0 in clock.sleeps

    def test_429_retries_are_bounded(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(429, headers={"Retry-After": "1"})

        client, _ = _client(handler, max_retries=2)
        result = client.check_availability(_hack())
        assert result.availability == Availability.ERROR
        assert result.raw_title == "HTTP 429"
        assert len(calls) == 3

    def test_429_without_retry_after_is_error_immediately(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(429)

        client, _ = _client(handler)
        assert client.check_availability(_hack()).availability == Availability.ERROR
        assert len(calls) == 1

    def test_429_with_long_retry_after_is_error_immediately(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(429, headers={"Retry-After": "3600"})

        client, clock = _client(handler)
        assert client.check_availability(_hack()).availability == Availability.ERROR
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
        client.check_availability(_hack())
        assert clock.sleeps == []
        client.check_availability(_hack())
        assert clock.sleeps == [0.5]

    def test_delay_is_shared_per_host_across_instances(self) -> None:
        clock = FakeClock()
        shared = HostThrottle(clock=clock.time, sleep=clock.sleep)
        a, _ = _client(lambda r: httpx.Response(404), delay=1.0, clock=clock, throttle=shared)
        b, _ = _client(lambda r: httpx.Response(404), delay=1.0, clock=clock, throttle=shared)
        a.check_availability(_hack())
        b.check_availability(_hack())
        assert clock.sleeps == [1.0]


class TestRdapLifecycle:
    def test_close_closes_owned_client(self) -> None:
        client = RdapRegistrarClient(BASE, delay=0.0)
        with client:
            pass
        assert client._client.is_closed

    def test_close_leaves_injected_client_open(self) -> None:
        http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
        with RdapRegistrarClient(BASE, delay=0.0, client=http):
            pass
        assert not http.is_closed
        http.close()

    def test_owned_client_has_a_short_connect_timeout(self) -> None:
        client = RdapRegistrarClient(BASE, delay=0.0, timeout=10.0)
        assert client._client.timeout.connect == 5.0
        assert client._client.timeout.read == 10.0
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
            assert client.check_availability(_hack(sld)).availability == Availability.ERROR
        result = client.check_availability(_hack("d"))
        assert len(calls) == 3
        assert result.availability == Availability.ERROR
        assert result.error_message == "skipped: rdap.example.test unresponsive (circuit open)"
        assert len(warnings) == 1

    def test_non_failure_errors_do_not_count(self) -> None:
        handler, calls = self._counting(lambda r: httpx.Response(400))
        warnings: list[str] = []
        client, _ = self._breaker_client(handler, warnings)
        for sld in ("a", "b", "c", "d"):
            client.check_availability(_hack(sld))
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
            client.check_availability(_hack(sld))
        state["fail"] = False
        assert client.check_availability(_hack("d")).availability == Availability.ERROR
        clock.now += 60.0
        assert client.check_availability(_hack("e")).availability == Availability.AVAILABLE
        assert client.check_availability(_hack("f")).availability == Availability.AVAILABLE

    def test_clients_sharing_a_breaker_share_the_circuit(self) -> None:
        clock = FakeClock()
        warnings: list[str] = []
        breaker = HostCircuitBreaker(threshold=3, clock=clock.time, on_open=warnings.append)
        handler, calls = self._counting(self._timeout)
        a, _ = _client(handler, clock=clock, breaker=breaker)
        b, _ = _client(handler, clock=clock, breaker=breaker)
        for sld in ("a", "b", "c"):
            a.check_availability(_hack(sld, "io"))
        assert "circuit open" in b.check_availability(_hack("x", "sh")).error_message
        assert len(calls) == 3
