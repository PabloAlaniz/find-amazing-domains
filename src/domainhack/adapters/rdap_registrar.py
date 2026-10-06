"""RDAP (RFC 9082/9083) availability checks: GET {base}domain/{fqdn}."""

from __future__ import annotations

import json
import random as _random
import time
from collections.abc import Callable
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx

from domainhack.adapters._circuit import HostCircuitBreaker
from domainhack.adapters._http import identity_headers
from domainhack.adapters._registration import normalize_statuses, parse_datetime_utc
from domainhack.adapters._throttle import (
    DEFAULT_BACKOFF_BASE,
    DEFAULT_BACKOFF_CAP,
    DEFAULT_MAX_INTERVAL,
    DEFAULT_THROTTLE,
    HostThrottle,
    full_jitter_backoff,
)
from domainhack.domain.entities import Availability, DomainCheckResult, DomainHack
from domainhack.ports.registrar import RegistrarClient

_ACCEPT = {"Accept": "application/rdap+json, application/json"}
_STATUS_TAKEN = 200
_STATUS_AVAILABLE = 404
_STATUS_TOO_MANY = 429
_STATUS_SERVER_ERROR = 500
_STATUS_UNAVAILABLE = 503
# Answers that tell us to send less: they slow the host's throttle down.
_SLOW_DOWN_STATUSES = frozenset({_STATUS_TOO_MANY, _STATUS_UNAVAILABLE})
# Transport errors worth one more try: timeouts and dropped connections.
_RETRYABLE_ERRORS = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
DEFAULT_CONNECT_TIMEOUT = 5.0


def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    """Parse a Retry-After header (delta-seconds or HTTP-date) into seconds."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    current = time.time() if now is None else now
    return max(when.timestamp() - current, 0.0)


class RdapRegistrarClient(RegistrarClient):
    """Checks availability against one RDAP base URL.

    The HTTP status decides: 404 -> AVAILABLE, 200 with a JSON domain object
    whose ``ldhName`` matches -> TAKEN, anything else (429, 5xx, timeouts,
    HTML 200 pages) -> ERROR. ``raw_title`` holds ``"HTTP <status>"``.
    TAKEN results carry the domain's ``status`` values and ``expiration``
    event when the body has them; malformed parts are ignored.

    429, 5xx, timeouts and dropped connections are retried up to
    ``max_retries`` times. A ``Retry-After`` header is honored (it defers the
    host on the shared ``throttle``); a 429 whose ``Retry-After`` exceeds
    ``max_retry_after`` is not retried. Without the header the retry waits a
    full-jitter exponential backoff, ``random(0, min(backoff_cap,
    backoff_base * 2**n))``. A 429, a 503 or a timeout also slows the host's
    throttle down; a real answer helps it recover.

    A check that still fails after its retries counts as one failure for the
    per-host circuit ``breaker`` (transport errors, 429, 5xx); while a host's
    circuit is open its domains get an ERROR without any network call.
    """

    def __init__(
        self,
        base_url: str,
        delay: float = 1.0,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
        *,
        max_retries: int = 2,
        max_retry_after: float = 30.0,
        throttle: HostThrottle | None = None,
        breaker: HostCircuitBreaker | None = None,
        random: Callable[[], float] = _random.random,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        backoff_cap: float = DEFAULT_BACKOFF_CAP,
        contact: str | None = None,
    ) -> None:
        self._headers = {**_ACCEPT, **identity_headers(contact)}
        self._base_url = base_url if base_url.endswith("/") else base_url + "/"
        self._host = httpx.URL(self._base_url).host
        self._delay = delay
        self._timeout = timeout
        self._owns_client = client is None
        self._client = client or httpx.Client(
            headers=self._headers,
            timeout=httpx.Timeout(timeout, connect=min(DEFAULT_CONNECT_TIMEOUT, timeout)),
            follow_redirects=True,
        )
        self._max_retries = max_retries
        self._max_retry_after = max_retry_after
        self._throttle = throttle if throttle is not None else DEFAULT_THROTTLE
        self._breaker = breaker if breaker is not None else HostCircuitBreaker()
        self._random = random
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def http_client(self) -> httpx.Client:
        """The underlying HTTP client: the injected one, or the one this instance owns."""
        return self._client

    def check_availability(self, domain: DomainHack) -> DomainCheckResult:
        if not self._breaker.allow(self._host):
            return _error(domain, self._breaker.skip_message(self._host))
        # fqdn is already a validated ASCII name; quoting the path segment is
        # defence in depth so no name can add path segments or a query string.
        url = f"{self._base_url}domain/{quote(domain.fqdn, safe='')}"
        # One check is one outcome: however many attempts it takes, it slows
        # the host down at most once and counts at most once for the breaker.
        slowed = False
        attempt = 0
        while True:
            self._throttle.wait(self._host, self._delay)
            try:
                response = self._client.get(url, headers=self._headers)
            except httpx.HTTPError as e:
                if isinstance(e, httpx.TimeoutException) and not slowed:
                    slowed = True
                    self._throttle.slow_down(self._host)
                if isinstance(e, _RETRYABLE_ERRORS) and attempt < self._max_retries:
                    self._backoff(attempt)
                    attempt += 1
                    continue
                self._breaker.record_failure(self._host)
                return _error(domain, f"RDAP request failed: {e or type(e).__name__}")

            status = response.status_code
            if status != _STATUS_TOO_MANY and status < _STATUS_SERVER_ERROR:
                self._throttle.record_success(self._host)
                self._breaker.record_success(self._host)
                return self._interpret(domain, response)

            if status in _SLOW_DOWN_STATUSES and not slowed:
                slowed = True
                self._throttle.slow_down(self._host)
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            if retry_after is not None:
                # RFC 7480 §5.5: honor Retry-After, for every client of this host.
                self._throttle.defer(self._host, min(retry_after, DEFAULT_MAX_INTERVAL))
            may_retry = retry_after is None or retry_after <= self._max_retry_after
            if may_retry and attempt < self._max_retries:
                if retry_after is None:
                    self._backoff(attempt)
                attempt += 1
                continue
            self._breaker.record_failure(self._host)
            if status == _STATUS_TOO_MANY:
                return _error(domain, "RDAP rate limited (429)", status)
            return self._interpret(domain, response)

    def _backoff(self, attempt: int) -> None:
        """Full-jitter exponential backoff before retry ``attempt`` (0-based).

        The wait goes through the throttle, so it overlaps with (rather than
        adds to) any slow-down already pending for the host.
        """
        delay = full_jitter_backoff(
            attempt, self._random, base=self._backoff_base, cap=self._backoff_cap
        )
        self._throttle.defer(self._host, delay)

    def _interpret(self, domain: DomainHack, response: httpx.Response) -> DomainCheckResult:
        status = response.status_code
        title = f"HTTP {status}"
        if status == _STATUS_AVAILABLE:
            return DomainCheckResult(
                domain=domain, availability=Availability.AVAILABLE, raw_title=title
            )
        if status != _STATUS_TAKEN:
            return _error(domain, f"Unexpected RDAP status {status}", status)

        try:
            body: Any = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _error(domain, "RDAP 200 response is not JSON", status)
        if not isinstance(body, dict):
            return _error(domain, "RDAP 200 response is not a JSON object", status)

        ldh = body.get("ldhName")
        if isinstance(ldh, str):
            if ldh.rstrip(".").lower() == domain.fqdn.lower():
                return _taken(domain, title, body)
            return _error(domain, f"RDAP ldhName mismatch: {ldh!r}", status)
        if body.get("objectClassName") == "domain":
            return _taken(domain, title, body)
        return _error(domain, "RDAP 200 response is not a domain object", status)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _taken(domain: DomainHack, title: str, body: dict[str, Any]) -> DomainCheckResult:
    return DomainCheckResult(
        domain=domain,
        availability=Availability.TAKEN,
        raw_title=title,
        statuses=parse_rdap_statuses(body),
        expires_at=parse_rdap_expiration(body),
    )


def parse_rdap_statuses(body: dict[str, Any]) -> tuple[str, ...]:
    """The domain's ``status`` array (RFC 9083 §4.6), normalized; ``()`` if absent or malformed."""
    values = body.get("status")
    if not isinstance(values, list):
        return ()
    return normalize_statuses(values)


def parse_rdap_expiration(body: dict[str, Any]) -> datetime | None:
    """The date of the first valid ``expiration`` event (RFC 9083 §4.5), in UTC, or None."""
    events = body.get("events")
    if not isinstance(events, list):
        return None
    for event in events:
        if not isinstance(event, dict):
            continue
        action = event.get("eventAction")
        if not isinstance(action, str) or action.strip().lower() != "expiration":
            continue
        when = parse_datetime_utc(event.get("eventDate"))
        if when is not None:
            return when
    return None


def _error(domain: DomainHack, message: str, status: int | None = None) -> DomainCheckResult:
    return DomainCheckResult(
        domain=domain,
        availability=Availability.ERROR,
        raw_title=f"HTTP {status}" if status is not None else "",
        error_message=message,
    )
