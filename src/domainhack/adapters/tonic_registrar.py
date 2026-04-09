import json
import time
from typing import ClassVar

import httpx

from domainhack.domain.entities import (
    Availability,
    DomainCheckResult,
    DomainHack,
)
from domainhack.ports.registrar import RegistrarClient


class TonicRegistrarClient(RegistrarClient):
    """Checks .to domain availability via tonic.to's web form."""

    _URL = "https://www.tonic.to/newcustform1.htm"
    _HEADERS: ClassVar[dict[str, str]] = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Origin": "https://www.tonic.to",
        "Referer": "https://www.tonic.to/newname.htm",
    }
    _TYPE_SUCCESS = "success"
    _TYPE_FAILURE = "failure"
    _STATUS_AVAILABLE = 200
    _STATUS_TAKEN = 409

    def __init__(self, delay: float = 1.0, timeout: float = 10.0) -> None:
        self._delay = delay
        self._timeout = timeout
        self._client = httpx.Client(headers=self._HEADERS, timeout=self._timeout)
        self._request_count = 0

    def check_availability(self, domain: DomainHack) -> DomainCheckResult:
        if self._request_count > 0 and self._delay > 0:
            time.sleep(self._delay)
        self._request_count += 1

        try:
            payload = {"domain": domain.sld}
            response = self._client.post(self._URL, data=payload)

            body = response.json()
            resp_type = body.get("type", "")
            status = body.get("status", 0)

            if resp_type == self._TYPE_SUCCESS and status == self._STATUS_AVAILABLE:
                availability = Availability.AVAILABLE
            elif resp_type == self._TYPE_FAILURE and status == self._STATUS_TAKEN:
                availability = Availability.TAKEN
            else:
                availability = Availability.ERROR

            return DomainCheckResult(
                domain=domain,
                availability=availability,
                raw_title=resp_type,
            )

        except (httpx.HTTPStatusError, httpx.RequestError, json.JSONDecodeError) as e:
            return DomainCheckResult(
                domain=domain,
                availability=Availability.ERROR,
                error_message=str(e),
            )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TonicRegistrarClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.close()
