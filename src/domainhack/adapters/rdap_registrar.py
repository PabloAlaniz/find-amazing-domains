"""RDAP (RFC 9082/9083) availability checks: GET {base}domain/{fqdn}."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx

from domainhack.adapters._circuit import HostCircuitBreaker
from domainhack.adapters._http import identity_headers
from domainhack.adapters._throttle import DEFAULT_THROTTLE, HostThrottle
from domainhack.domain.entities import Availability, DomainCheckResult, DomainHack
from domainhack.ports.registrar import RegistrarClient

_ACCEPT = {"Accept": "application/rdap+json, application/json"}
_STATUS_TAKEN = 200
_STATUS_AVAILABLE = 404
_STATUS_TOO_MANY = 429
_STATUS_SERVER_ERROR = 500
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
    HTML 200 pages) -> ERROR. A 429 with a short ``Retry-After`` is retried a
    bounded number of times. ``raw_title`` holds ``"HTTP <status>"``.

    Transport errors, a final 429 and 5xx answers count as failures for the
    per-host circuit ``breaker``; while a host's circuit is open its domains
    get an ERROR without any network call.
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
        sleep: Callable[[float], None] = time.sleep,
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
        self._sleep = sleep

    @property
    def base_url(self) -> str:
        return self._base_url

    def check_availability(self, domain: DomainHack) -> DomainCheckResult:
        if not self._breaker.allow(self._host):
            return _error(domain, self._breaker.skip_message(self._host))
        # fqdn is already a validated ASCII name; quoting the path segment is
        # defence in depth so no name can add path segments or a query string.
        url = f"{self._base_url}domain/{quote(domain.fqdn, safe='')}"
        attempt = 0
        while True:
            self._throttle.wait(self._host, self._delay)
            try:
                response = self._client.get(url, headers=self._headers)
            except httpx.HTTPError as e:
                self._breaker.record_failure(self._host)
                return _error(domain, f"RDAP request failed: {e or type(e).__name__}")

            if response.status_code == _STATUS_TOO_MANY:
                wait = parse_retry_after(response.headers.get("Retry-After"))
                if (
                    attempt < self._max_retries
                    and wait is not None
                    and wait <= self._max_retry_after
                ):
                    attempt += 1
                    self._sleep(wait)
                    continue
                self._breaker.record_failure(self._host)
                return _error(domain, "RDAP rate limited (429)", response.status_code)

            if response.status_code >= _STATUS_SERVER_ERROR:
                self._breaker.record_failure(self._host)
            else:
                self._breaker.record_success(self._host)
            return self._interpret(domain, response)

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
                return DomainCheckResult(
                    domain=domain, availability=Availability.TAKEN, raw_title=title
                )
            return _error(domain, f"RDAP ldhName mismatch: {ldh!r}", status)
        if body.get("objectClassName") == "domain":
            return DomainCheckResult(
                domain=domain, availability=Availability.TAKEN, raw_title=title
            )
        return _error(domain, "RDAP 200 response is not a domain object", status)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _error(domain: DomainHack, message: str, status: int | None = None) -> DomainCheckResult:
    return DomainCheckResult(
        domain=domain,
        availability=Availability.ERROR,
        raw_title=f"HTTP {status}" if status is not None else "",
        error_message=message,
    )
