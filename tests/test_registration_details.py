"""Registration details (statuses, expiration) from RDAP and WHOIS replies."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from domainhack.adapters._registration import (
    format_utc,
    normalize_status,
    normalize_statuses,
    parse_datetime_utc,
)
from domainhack.adapters._throttle import HostThrottle
from domainhack.adapters.rdap_registrar import (
    RdapRegistrarClient,
    parse_rdap_expiration,
    parse_rdap_statuses,
)
from domainhack.adapters.whois_registrar import (
    WhoisRegistrarClient,
    _server,
    parse_whois_expiration,
    parse_whois_statuses,
)
from domainhack.domain.entities import (
    DROPPING_STATUSES,
    Availability,
    DomainCheckResult,
    DomainHack,
)
from tests.fakes import FakeClock, hack

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rdap"


def _fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)  # type: ignore[misc]


def _rdap_check(body: object, domain: DomainHack) -> DomainCheckResult:
    clock = FakeClock()
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    client = RdapRegistrarClient(
        "https://rdap.example.test/",
        delay=0.0,
        client=http,
        throttle=HostThrottle(clock=clock.time, sleep=clock.sleep),
        sleep=clock.sleep,
    )
    return client.check_availability(domain)


class TestParseDatetime:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("2027-09-30T01:00:00Z", _utc(2027, 9, 30, 1, 0, 0)),
            ("2026-11-30T07:38:29.000Z", _utc(2026, 11, 30, 7, 38, 29)),
            ("2026-11-30T07:38:29.123456789Z", _utc(2026, 11, 30, 7, 38, 29)),
            ("2026-11-30t07:38:29z", _utc(2026, 11, 30, 7, 38, 29)),
            ("2027-01-05 10:00:00+01:00", _utc(2027, 1, 5, 9, 0, 0)),
            ("2027-01-05T10:00:00-0300", _utc(2027, 1, 5, 13, 0, 0)),
            ("2027-01-05T10:00+05", _utc(2027, 1, 5, 5, 0, 0)),
            ("2027-01-05T10:00:00", _utc(2027, 1, 5, 10, 0, 0)),  # no offset: UTC
            ("2027-01-05", _utc(2027, 1, 5)),
            ("  2027-01-05  ", _utc(2027, 1, 5)),
        ],
    )
    def test_valid(self, text: str, expected: datetime) -> None:
        parsed = parse_datetime_utc(text)
        assert parsed == expected
        assert parsed is not None and parsed.utcoffset() == timedelta(0)

    @pytest.mark.parametrize(
        "value",
        [
            None,
            12345,
            ["2027-01-05"],
            "",
            "soon",
            "30-Nov-2026",
            "30.11.2026",
            "2027-13-01",
            "2027-02-30T00:00:00Z",
            "2027-01-05T25:00:00Z",
            "2027-01-05T10:00:00+25:00",
            "2027-01-05T10:00:00Z trailing",
        ],
    )
    def test_invalid_is_none(self, value: object) -> None:
        assert parse_datetime_utc(value) is None

    def test_format_utc(self) -> None:
        assert format_utc(None) == ""
        assert format_utc(_utc(2026, 11, 30, 7, 38, 29)) == "2026-11-30T07:38:29Z"
        plus_one = datetime(2027, 1, 5, 10, tzinfo=timezone(timedelta(hours=1)))
        assert format_utc(plus_one) == "2027-01-05T09:00:00Z"


class TestNormalizeStatus:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("active", "active"),
            ("pending delete", "pending delete"),
            ("Pending  Delete", "pending delete"),
            ("pendingDelete", "pending delete"),
            ("redemptionPeriod", "redemption period"),
            ("clientTransferProhibited", "client transfer prohibited"),
            ("server_hold", "server hold"),
            ("ok", "ok"),
        ],
    )
    def test_normalizes(self, value: str, expected: str) -> None:
        assert normalize_status(value) == expected

    @pytest.mark.parametrize("value", [None, 3, {"x": 1}, "", "   ", "x" * 65, "bad\x00status"])
    def test_rejects(self, value: object) -> None:
        assert normalize_status(value) is None

    def test_list_drops_invalid_and_duplicates(self) -> None:
        values = ["active", 7, "pendingDelete", "pending delete", None, "Active"]
        assert normalize_statuses(values) == ("active", "pending delete")


class TestDroppingDetection:
    def _result(self, statuses: tuple[str, ...], availability: Availability) -> DomainCheckResult:
        return DomainCheckResult(domain=hack(), availability=availability, statuses=statuses)

    @pytest.mark.parametrize("status", sorted(DROPPING_STATUSES))
    def test_dropping_statuses(self, status: str) -> None:
        result = self._result(("client hold", status), Availability.TAKEN)
        assert result.is_dropping
        assert result.dropping_statuses == (status,)

    @pytest.mark.parametrize(
        "statuses",
        [(), ("active",), ("client hold", "server hold"), ("pending restore",)],
    )
    def test_not_dropping(self, statuses: tuple[str, ...]) -> None:
        assert not self._result(statuses, Availability.TAKEN).is_dropping

    def test_only_taken_names_are_dropping(self) -> None:
        result = self._result(("pending delete",), Availability.ERROR)
        assert result.dropping_statuses == ("pending delete",)
        assert not result.is_dropping

    def test_defaults_are_backward_compatible(self) -> None:
        result = DomainCheckResult(domain=hack(), availability=Availability.TAKEN)
        assert result.statuses == ()
        assert result.expires_at is None
        assert not result.is_dropping


class TestRdapFixtures:
    def test_identity_digital(self) -> None:
        result = _rdap_check(_fixture("identity_digital_google_io.json"), hack("google", "io"))
        assert result.availability is Availability.TAKEN
        assert result.statuses == (
            "client delete prohibited",
            "server delete prohibited",
            "client transfer prohibited",
            "server transfer prohibited",
            "client update prohibited",
            "server update prohibited",
        )
        assert result.expires_at == _utc(2027, 9, 30, 1, 0, 0)
        assert not result.is_dropping

    def test_tonic(self) -> None:
        result = _rdap_check(_fixture("tonic_google_to.json"), hack("google", "to"))
        assert result.availability is Availability.TAKEN
        assert result.statuses[0] == "client delete prohibited"
        assert result.expires_at == _utc(2026, 11, 30, 7, 38, 29)

    def test_pending_delete(self) -> None:
        result = _rdap_check(_fixture("pending_delete_example_io.json"), hack("example", "io"))
        assert result.is_dropping
        assert result.dropping_statuses == ("pending delete", "redemption period")
        assert result.statuses[-1] == "server hold"
        assert result.expires_at == _utc(2026, 8, 28, 12, 0, 0)

    def test_domain_object_without_ldh_name_also_gets_details(self) -> None:
        body = {"objectClassName": "domain", "status": ["redemption period"]}
        result = _rdap_check(body, hack())
        assert result.availability is Availability.TAKEN
        assert result.is_dropping

    def test_available_and_error_have_no_details(self) -> None:
        result = _rdap_check({"ldhName": "other.to", "status": ["active"]}, hack())
        assert result.availability is Availability.ERROR
        assert result.statuses == ()
        assert result.expires_at is None


class TestRdapMalformedParts:
    """A malformed status or events part never turns TAKEN into ERROR."""

    @pytest.mark.parametrize(
        "extra",
        [
            {"status": "active"},
            {"status": None},
            {"status": {"active": True}},
            {"status": [1, None, ["x"], ""]},
            {"events": "expiration"},
            {"events": None},
            {"events": [None, 3, "x", []]},
            {"events": [{"eventAction": "expiration"}]},
            {"events": [{"eventAction": "expiration", "eventDate": 1790000000}]},
            {"events": [{"eventAction": "expiration", "eventDate": "next year"}]},
            {"events": [{"eventAction": ["expiration"], "eventDate": "2027-01-01"}]},
            {"events": [{"eventDate": "2027-01-01T00:00:00Z"}]},
        ],
    )
    def test_still_taken_with_empty_details(self, extra: dict[str, Any]) -> None:
        body = {"objectClassName": "domain", "ldhName": "pla.to", **extra}
        result = _rdap_check(body, hack())
        assert result.availability is Availability.TAKEN
        assert result.statuses == ()
        assert result.expires_at is None

    def test_keeps_valid_statuses_among_invalid_ones(self) -> None:
        assert parse_rdap_statuses({"status": [None, "active", 4, "Client Hold"]}) == (
            "active",
            "client hold",
        )

    def test_first_valid_expiration_event_wins(self) -> None:
        body = {
            "events": [
                {"eventAction": "registration", "eventDate": "2001-01-01T00:00:00Z"},
                {"eventAction": "expiration", "eventDate": "garbage"},
                {"eventAction": " Expiration ", "eventDate": "2028-02-03T04:05:06Z"},
                {"eventAction": "expiration", "eventDate": "2029-01-01T00:00:00Z"},
            ]
        }
        assert parse_rdap_expiration(body) == _utc(2028, 2, 3, 4, 5, 6)


ICANN_STYLE_REPLY = """\
Domain Name: pla.in
Registry Domain ID: D123-IN
Updated Date: 2026-09-01T10:00:00Z
Creation Date: 2010-01-01T00:00:00Z
Registry Expiry Date: 2026-11-02T08:00:00Z
Domain Status: pendingDelete https://icann.org/epp#pendingDelete
Domain Status: redemptionPeriod https://icann.org/epp#redemptionPeriod
Domain Status: clientTransferProhibited https://icann.org/epp#clientTransferProhibited
"""

IT_STYLE_REPLY = """\
Domain:             pla.it
Status:             ok
Signed:             no
Created:            2001-01-01 00:00:00
Last Update:        2026-01-02 00:52:31
Expire Date:        2027-01-01
"""


class TestWhoisDetails:
    def test_icann_style(self) -> None:
        assert parse_whois_statuses(ICANN_STYLE_REPLY) == (
            "pending delete",
            "redemption period",
            "client transfer prohibited",
        )
        assert parse_whois_expiration(ICANN_STYLE_REPLY) == _utc(2026, 11, 2, 8, 0, 0)

    def test_it_style(self) -> None:
        assert parse_whois_statuses(IT_STYLE_REPLY) == ("ok",)
        assert parse_whois_expiration(IT_STYLE_REPLY) == _utc(2027, 1, 1)

    @pytest.mark.parametrize(
        "text",
        [
            "Status: NOT AVAILABLE\nExpiry Date: 30-Nov-2026\n",
            "state: active\nexpires: 2027-01-01\n",
            "Domain Status: No Object Found\n",
            "",
        ],
    )
    def test_unrecognised_formats_are_empty(self, text: str) -> None:
        assert parse_whois_statuses(text) == ()
        assert parse_whois_expiration(text) is None

    def test_first_parseable_expiry_wins(self) -> None:
        text = "Expiration Date: 01.01.2027\nRegistry Expiry Date: 2028-05-06T00:00:00Z\n"
        assert parse_whois_expiration(text) == _utc(2028, 5, 6)

    def test_client_attaches_details_to_taken(self) -> None:
        clock = FakeClock()

        class Conn:
            def __init__(self) -> None:
                self._reply = ICANN_STYLE_REPLY.encode()

            def settimeout(self, value: float | None, /) -> None:
                pass

            def sendall(self, data: bytes, /) -> None:
                pass

            def recv(self, bufsize: int, /) -> bytes:
                out, self._reply = self._reply, b""
                return out

            def close(self) -> None:
                pass

        client = WhoisRegistrarClient(
            delay=0.0,
            servers={"in": _server("whois.example.test", r"is available for registration")},
            connect=lambda address, timeout: Conn(),
            throttle=HostThrottle(clock=clock.time, sleep=clock.sleep),
        )
        result = client.check_availability(hack("pla", "in"))
        assert result.availability is Availability.TAKEN
        assert result.is_dropping
        assert result.expires_at == _utc(2026, 11, 2, 8, 0, 0)
