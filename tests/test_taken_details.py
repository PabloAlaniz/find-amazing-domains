"""Details of taken names: registration date, registrar, nameservers, parking hint.

Parsing (RDAP and WHOIS), the parking classifier, the CSV/JSON columns, the
console TAKEN line and the cache (round trip plus migration from a v1 file).
"""

import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from domainhack.adapters._registration import (
    normalize_hostname,
    normalize_name,
    normalize_nameservers,
)
from domainhack.adapters.cached_registrar import (
    CACHE_RAW_TITLE,
    DAY,
    SCHEMA_VERSION,
    CachedRegistrarClient,
)
from domainhack.adapters.console_writer import ConsoleResultWriter
from domainhack.adapters.csv_writer import CSV_FIELDS, CsvResultWriter
from domainhack.adapters.json_writer import JsonResultWriter
from domainhack.adapters.rdap_registrar import (
    RdapRegistrarClient,
    parse_rdap_nameservers,
    parse_rdap_registrar,
    parse_rdap_registration,
)
from domainhack.adapters.whois_registrar import (
    WhoisRegistrarClient,
    _server,
    parse_whois_creation,
    parse_whois_nameservers,
)
from domainhack.domain.entities import Availability, DnsEvidence, DomainCheckResult, DomainHack
from domainhack.domain.parking import PARKING_NAMESERVERS, parking_hint_for
from tests.fakes import Answer, FakeClock, FakeRandom, ScriptedRegistrar, fake_throttle, hack

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rdap"
NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z
REGISTERED = datetime(2015, 11, 12, 19, 56, 14, tzinfo=timezone.utc)
EXPIRES = datetime(2026, 11, 12, 19, 56, 14, tzinfo=timezone.utc)
DOMAINRECOVER = ("ns1.domainrecover.com", "ns2.domainrecover.com")


def _fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def _rdap_check(body: object, domain: DomainHack) -> DomainCheckResult:
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    client = RdapRegistrarClient(
        "https://rdap.example.test/",
        delay=0.0,
        client=http,
        throttle=fake_throttle(FakeClock()),
        random=FakeRandom(),
    )
    return client.check_availability(domain)


def _sumanda(**overrides: Any) -> DomainCheckResult:
    fields: dict[str, Any] = {
        "domain": hack("sumanda", "com"),
        "availability": Availability.TAKEN,
        "statuses": ("client transfer prohibited",),
        "expires_at": EXPIRES,
        "registered_at": REGISTERED,
        "registrar": "Nikko Reg LLP",
        "nameservers": DOMAINRECOVER,
        "parked_hint": "domainrecover",
    }
    fields.update(overrides)
    return DomainCheckResult(**fields)


class TestRdapFixtures:
    def test_sumanda_com_verisign(self) -> None:
        result = _rdap_check(_fixture("verisign_sumanda_com.json"), hack("sumanda", "com"))
        assert result.availability is Availability.TAKEN
        assert result.registered_at == REGISTERED
        assert result.expires_at == EXPIRES
        assert result.registrar == "Nikko Reg LLP"
        assert result.nameservers == DOMAINRECOVER
        assert result.parked_hint == "domainrecover"

    def test_google_io_identity_digital(self) -> None:
        result = _rdap_check(_fixture("identity_digital_google_io.json"), hack("google", "io"))
        assert result.registered_at == datetime(2002, 10, 1, 1, tzinfo=timezone.utc)
        # The registrant (empty fn) is skipped; the registrar entity wins.
        assert result.registrar == "MarkMonitor Inc."
        assert result.nameservers == (
            "ns1.google.com",
            "ns4.google.com",
            "ns3.google.com",
            "ns2.google.com",
        )
        assert result.parked_hint == ""

    def test_fixture_without_details(self) -> None:
        result = _rdap_check(_fixture("tonic_google_to.json"), hack("google", "to"))
        assert result.availability is Availability.TAKEN
        assert (result.registrar, result.parked_hint) == ("", "")


class TestRdapParsing:
    def test_registration_event(self) -> None:
        body = {
            "events": [
                "junk",
                {"eventAction": 7},
                {"eventAction": "registration", "eventDate": "not a date"},
                {"eventAction": " Registration ", "eventDate": "2015-11-12T19:56:14Z"},
            ]
        }
        assert parse_rdap_registration(body) == REGISTERED

    @pytest.mark.parametrize("events", [None, "x", {}, [], [{"eventAction": "expiration"}]])
    def test_registration_missing(self, events: object) -> None:
        assert parse_rdap_registration({"events": events}) is None

    def test_nameservers_normalized_and_deduped(self) -> None:
        body = {
            "nameservers": [
                {"ldhName": "NS2.Example.COM."},
                {"ldhName": "ns1.example.com"},
                {"ldhName": "ns2.example.com"},
                {"ldhName": ""},
                {"ldhName": "bad host!"},
                {"ldhName": 42},
                {"unicodeName": "ns3.example.com"},
                "ns4.example.com",
                None,
            ]
        }
        assert parse_rdap_nameservers(body) == ("ns2.example.com", "ns1.example.com")

    @pytest.mark.parametrize("value", [None, "ns1.x.com", {"ldhName": "ns1.x.com"}, 3])
    def test_nameservers_malformed_container(self, value: object) -> None:
        assert parse_rdap_nameservers({"nameservers": value}) == ()

    def test_registrar_fn(self) -> None:
        body = {
            "entities": [
                {"roles": ["technical"], "vcardArray": ["vcard", [["fn", {}, "text", "Tech"]]]},
                {
                    "roles": ["REGISTRAR"],
                    "handle": "840",
                    "vcardArray": [
                        "vcard",
                        [["version", {}, "text", "4.0"], ["fn", {}, "text", "  Nikko   Reg LLP "]],
                    ],
                },
            ]
        }
        assert parse_rdap_registrar(body) == "Nikko Reg LLP"

    @pytest.mark.parametrize(
        "vcard",
        [
            None,
            "vcard",
            ["vcard"],
            ["vcard", "x"],
            ["vcard", [["fn", {}, "text", ""]]],
            ["vcard", [["fn", {}, "text"]]],
            ["vcard", [["fn", {}, "text", ["structured"]]]],
            ["vcard", [["fn", {}, "text", "bad\x00name"]]],
            ["vcard", ["notalist"]],
        ],
    )
    def test_registrar_falls_back_to_handle(self, vcard: object) -> None:
        body = {"entities": [{"roles": ["registrar"], "handle": "292", "vcardArray": vcard}]}
        assert parse_rdap_registrar(body) == "292"

    @pytest.mark.parametrize(
        "entities",
        [
            None,
            "x",
            [],
            ["x"],
            [{"roles": "registrar", "handle": "1"}],
            [{"roles": [None, "registrant"], "handle": "1"}],
            [{"roles": ["registrar"], "handle": 5}],
            [{"roles": ["registrar"]}],
        ],
    )
    def test_registrar_missing(self, entities: object) -> None:
        assert parse_rdap_registrar({"entities": entities}) == ""

    def test_second_registrar_entity_used_when_first_is_empty(self) -> None:
        body = {"entities": [{"roles": ["registrar"]}, {"roles": ["registrar"], "handle": "9"}]}
        assert parse_rdap_registrar(body) == "9"

    def test_malformed_details_never_turn_taken_into_error(self) -> None:
        body = {
            "objectClassName": "domain",
            "ldhName": "sumanda.com",
            "events": "nope",
            "nameservers": {"ldhName": 1},
            "entities": [{"roles": ["registrar"], "vcardArray": [None, None]}],
        }
        result = _rdap_check(body, hack("sumanda", "com"))
        assert result.availability is Availability.TAKEN
        assert result.registered_at is None
        assert (result.registrar, result.nameservers, result.parked_hint) == ("", (), "")


class TestNormalizers:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("NS1.Example.COM.", "ns1.example.com"),
            (" ns_1.example.com ", "ns_1.example.com"),
            ("localhost", "localhost"),
            ("", None),
            (".", None),
            ("a..b", None),
            ("-a.com", None),
            ("a b.com", None),
            ("x" * 254, None),
            (None, None),
        ],
    )
    def test_hostname(self, value: object, expected: str | None) -> None:
        assert normalize_hostname(value) == expected

    def test_nameservers(self) -> None:
        assert normalize_nameservers(["A.com", "a.com.", 1, "b.com"]) == ("a.com", "b.com")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("  Foo\n Bar ", "Foo Bar"), ("", None), ("x" * 201, None), (3, None), ("a\x07", None)],
    )
    def test_name(self, value: object, expected: str | None) -> None:
        assert normalize_name(value) == expected


WHOIS_TAKEN = """\
Domain Name: EXAMPLE.IT
Creation Date: 2015-11-12T19:56:14Z
Registry Expiry Date: 2026-11-12T19:56:14Z
Name Server: NS1.SEDOPARKING.COM
Name Server: ns2.sedoparking.com.
Name Server: ns1.sedoparking.com
Name Server:
nserver: dns.example.net 192.0.2.1
"""


class TestWhois:
    def test_nameservers(self) -> None:
        assert parse_whois_nameservers(WHOIS_TAKEN) == (
            "ns1.sedoparking.com",
            "ns2.sedoparking.com",
            "dns.example.net",
        )

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("Creation Date: 2015-11-12T19:56:14Z", REGISTERED),
            ("Created: 2015-11-12 19:56:14", REGISTERED),
            ("created:  2015-11-12T19:56:14+00:00", REGISTERED),
            ("Registration Time: 2015-11-12 19:56:14", REGISTERED),
            ("Created On: 12-Nov-2015", None),
            ("Registrar Registration Expiration Date: 2026-11-12T19:56:14Z", None),
        ],
    )
    def test_creation(self, line: str, expected: datetime | None) -> None:
        assert parse_whois_creation(f"Domain: x\n{line}\n") == expected

    def test_creation_skips_unparseable_then_finds_iso(self) -> None:
        text = "Created: yesterday\nCreation Date: 2015-11-12T19:56:14Z\n"
        assert parse_whois_creation(text) == REGISTERED

    def test_taken_result_carries_details(self) -> None:
        class Conn:
            def __init__(self) -> None:
                self._chunks = [WHOIS_TAKEN.encode(), b""]

            def settimeout(self, value: float | None) -> None:
                pass

            def sendall(self, data: bytes) -> None:
                pass

            def recv(self, bufsize: int) -> bytes:
                return self._chunks.pop(0)

            def close(self) -> None:
                pass

        client = WhoisRegistrarClient(
            delay=0.0,
            servers={"it": _server("whois.example.test", r"AVAILABLE")},
            connect=lambda address, timeout: Conn(),
            throttle=fake_throttle(FakeClock()),
            random=FakeRandom(),
        )
        result = client.check_availability(hack("example", "it"))
        assert result.availability is Availability.TAKEN
        assert result.registered_at == REGISTERED
        assert result.nameservers[:2] == ("ns1.sedoparking.com", "ns2.sedoparking.com")
        assert result.parked_hint == "sedo"


class TestParking:
    @pytest.mark.parametrize(
        ("nameservers", "expected"),
        [
            (["NS1.DOMAINRECOVER.COM", "NS2.DOMAINRECOVER.COM"], "domainrecover"),
            (["ns1.sedoparking.com."], "sedo"),
            (["  NS2.ParkingCrew.NET.  "], "parkingcrew"),
            (["ns1.bodis.com"], "bodis"),
            (["ns1.afternic.com"], "afternic"),
            (["ns1.dan.com"], "dan.com"),
            (["ns1.hugedomains.com"], "hugedomains"),
            (["ns01.cashparking.com"], "godaddy-parked"),
            (["ns1.namebrightdns.com"], "namebright"),
            (["deep.ns.above.com"], "above"),
            (["sedoparking.com"], "sedo"),  # the apex itself
            (["ns1.google.com", "ns1.sedoparking.com"], "sedo"),  # first match decides
            (["ns1.afternic.com", "ns1.sedoparking.com"], "afternic"),
            (["ns1.notsedoparking.com"], ""),  # suffix match is per label
            (["sedoparking.com.evil.net"], ""),
            (["ns1.domaincontrol.com"], ""),
            ([], ""),
            (["", ".", None, 7], ""),
        ],
    )
    def test_hint(self, nameservers: list[Any], expected: str) -> None:
        assert parking_hint_for(nameservers) == expected

    def test_accepts_any_iterable(self) -> None:
        assert parking_hint_for(ns for ns in DOMAINRECOVER) == "domainrecover"

    def test_mapping_is_lowercase_without_dots(self) -> None:
        for suffix, provider in PARKING_NAMESERVERS.items():
            assert suffix == suffix.lower().strip(".") and "." in suffix
            assert provider and provider == provider.lower()


class TestWriters:
    def test_csv_columns_appended_after_expires_at(self) -> None:
        assert CSV_FIELDS[CSV_FIELDS.index("expires_at") + 1 :] == (
            "registered_at",
            "registrar",
            "nameservers",
            "parked_hint",
            "dns_nameservers",
            "dns_conflict",
        )

    def test_csv_row(self) -> None:
        buf = io.StringIO()
        writer = CsvResultWriter(buf)
        writer.write_result(_sumanda(dns=DnsEvidence(nameservers=DOMAINRECOVER)))
        conflict = DomainCheckResult(
            domain=hack("free"),
            availability=Availability.AVAILABLE,
            dns=DnsEvidence(nameservers=("ns1.x.net",), has_address=True),
        )
        writer.write_result(conflict)
        first, second = csv.DictReader(io.StringIO(buf.getvalue()))
        assert first["registered_at"] == "2015-11-12T19:56:14Z"
        assert first["registrar"] == "Nikko Reg LLP"
        assert first["nameservers"] == "ns1.domainrecover.com;ns2.domainrecover.com"
        assert first["parked_hint"] == "domainrecover"
        assert first["dns_nameservers"] == "ns1.domainrecover.com;ns2.domainrecover.com"
        assert first["dns_conflict"] == "false"
        assert second["dns_nameservers"] == "ns1.x.net"
        assert second["dns_conflict"] == "true"

    def test_json_record(self) -> None:
        buf = io.StringIO()
        writer = JsonResultWriter(buf)
        writer.write_result(_sumanda())
        writer.write_result(
            DomainCheckResult(
                domain=hack("free"),
                availability=Availability.AVAILABLE,
                dns=DnsEvidence(nameservers=("ns1.x.net",)),
            )
        )
        first, second = (json.loads(line) for line in buf.getvalue().splitlines())
        assert list(first)[-6:] == [
            "registered_at",
            "registrar",
            "nameservers",
            "parked_hint",
            "dns_nameservers",
            "dns_conflict",
        ]
        assert first["registered_at"] == "2015-11-12T19:56:14Z"
        assert first["registrar"] == "Nikko Reg LLP"
        assert first["nameservers"] == list(DOMAINRECOVER)
        assert first["parked_hint"] == "domainrecover"
        assert first["dns_nameservers"] == []
        assert first["dns_conflict"] is False
        assert second["dns_nameservers"] == ["ns1.x.net"]
        assert second["dns_conflict"] is True


class TestConsoleTakenLine:
    def test_full_detail(self, capsys: pytest.CaptureFixture[str]) -> None:
        ConsoleResultWriter(show_taken=True).write_result(_sumanda())
        assert capsys.readouterr().out == (
            "  TAKEN:     sumanda.com"
            " (since 2015-11-12, expires 2026-11-12, parked: domainrecover)\n"
        )

    @pytest.mark.parametrize(
        ("overrides", "suffix"),
        [
            ({"expires_at": None}, " (since 2015-11-12, parked: domainrecover)"),
            ({"registered_at": None, "parked_hint": ""}, " (expires 2026-11-12)"),
            ({"registered_at": None, "expires_at": None}, " (parked: domainrecover)"),
            ({"registered_at": None, "expires_at": None, "parked_hint": ""}, ""),
        ],
    )
    def test_only_known_parts(
        self, capsys: pytest.CaptureFixture[str], overrides: dict[str, Any], suffix: str
    ) -> None:
        ConsoleResultWriter(show_taken=True).write_result(_sumanda(**overrides))
        assert capsys.readouterr().out == f"  TAKEN:     sumanda.com{suffix}\n"

    def test_hidden_without_show_taken(self, capsys: pytest.CaptureFixture[str]) -> None:
        ConsoleResultWriter().write_result(_sumanda())
        assert capsys.readouterr().out == ""

    def test_dropping_format_unchanged(self, capsys: pytest.CaptureFixture[str]) -> None:
        ConsoleResultWriter(show_taken=True).write_result(_sumanda(statuses=("pending delete",)))
        assert capsys.readouterr().out == (
            "  TAKEN (dropping: pending delete, expires 2026-11-12): sumanda.com\n"
        )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "results.sqlite3"


SUMANDA_ANSWER = Answer(
    statuses=("client transfer prohibited",),
    expires_at=EXPIRES,
    registered_at=REGISTERED,
    registrar="Nikko Reg LLP",
    nameservers=DOMAINRECOVER,
    parked_hint="domainrecover",
)


class TestCache:
    def test_round_trip(self, db_path: Path) -> None:
        clock = FakeClock(NOW)
        with CachedRegistrarClient(
            ScriptedRegistrar({"sumanda.com": SUMANDA_ANSWER}), path=db_path, clock=clock
        ) as cached:
            cached.check_availability(hack("sumanda", "com"))
        inner = ScriptedRegistrar()
        with CachedRegistrarClient(inner, path=db_path, clock=clock) as cached:
            result = cached.check_availability(hack("sumanda", "com"))
        assert inner.calls == []
        assert result.raw_title == CACHE_RAW_TITLE
        assert result.registered_at == REGISTERED
        assert result.registrar == "Nikko Reg LLP"
        assert result.nameservers == DOMAINRECOVER
        assert result.parked_hint == "domainrecover"
        assert result.dns is None

    def test_dns_is_not_stored(self, db_path: Path) -> None:
        class WithDns(ScriptedRegistrar):
            def check_availability(self, domain: DomainHack) -> DomainCheckResult:
                result = super().check_availability(domain)
                return DomainCheckResult(
                    domain=result.domain,
                    availability=result.availability,
                    dns=DnsEvidence(nameservers=("ns1.x.net",)),
                )

        clock = FakeClock(NOW)
        with CachedRegistrarClient(WithDns(), path=db_path, clock=clock) as cached:
            assert cached.check_availability(hack()).dns is not None
        with CachedRegistrarClient(ScriptedRegistrar(), path=db_path, clock=clock) as cached:
            assert cached.check_availability(hack()).dns is None
        conn = sqlite3.connect(db_path)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(results)")}
        finally:
            conn.close()
        assert not any(c.startswith("dns") for c in columns)

    @pytest.mark.parametrize(
        ("registered_at", "registrar", "nameservers", "parked_hint"),
        [
            ("x", "bad\x00", "not json", "a\x00b"),
            (1e300, "", '{"a": 1}', ""),
            (None, " ", "[1]", "   "),
        ],
    )
    def test_malformed_stored_details_are_ignored(
        self,
        db_path: Path,
        registered_at: object,
        registrar: object,
        nameservers: object,
        parked_hint: object,
    ) -> None:
        clock = FakeClock(NOW)
        with CachedRegistrarClient(ScriptedRegistrar(), path=db_path, clock=clock) as cached:
            cached.check_availability(hack())
        conn = sqlite3.connect(db_path)
        with conn:
            conn.execute(
                "UPDATE results SET registered_at = ?, registrar = ?, nameservers = ?,"
                " parked_hint = ?",
                (registered_at, registrar, nameservers, parked_hint),
            )
        conn.close()
        with CachedRegistrarClient(ScriptedRegistrar(), path=db_path, clock=clock) as cached:
            result = cached.check_availability(hack())
        assert result.raw_title == CACHE_RAW_TITLE
        assert result.availability is Availability.TAKEN
        assert result.registered_at is None
        assert (result.registrar, result.nameservers, result.parked_hint) == ("", (), "")


def _make_v1_db(path: Path) -> None:
    """A cache file as written by schema version 1 (statuses and expiration only)."""
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(
            "CREATE TABLE results ("
            " fqdn TEXT PRIMARY KEY,"
            " availability TEXT NOT NULL,"
            " checked_at REAL NOT NULL,"
            " statuses TEXT NOT NULL DEFAULT '',"
            " expires_at REAL)"
        )
        conn.execute(
            "INSERT INTO results VALUES (?, ?, ?, ?, ?)",
            ("pla.to", "taken", NOW - DAY, '["client hold"]', EXPIRES.timestamp() + 365 * DAY),
        )
        conn.execute("PRAGMA user_version = 1")
    conn.close()


class TestMigrationFromV1:
    def test_v1_taken_rows_are_refetched_and_new_rows_store_details(self, db_path: Path) -> None:
        _make_v1_db(db_path)
        inner = ScriptedRegistrar({"sumanda.com": SUMANDA_ANSWER})
        clock = FakeClock(NOW)
        with CachedRegistrarClient(inner, path=db_path, clock=clock) as cached:
            old = cached.check_availability(hack())
            cached.check_availability(hack("sumanda", "com"))
            assert not cached.disabled
        # The v1 TAKEN row had no registration details, so the migration
        # expired it instead of serving it until its expiry date.
        assert old.raw_title == "live"
        assert inner.calls == ["pla.to", "sumanda.com"]

        conn = sqlite3.connect(db_path)
        try:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(results)")]
            assert columns[5:] == ["registered_at", "registrar", "nameservers", "parked_hint"]
            assert conn.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,) == (2,)
            row = conn.execute(
                "SELECT registrar, nameservers, parked_hint FROM results WHERE fqdn = ?",
                ("sumanda.com",),
            ).fetchone()
        finally:
            conn.close()
        assert row == ("Nikko Reg LLP", json.dumps(list(DOMAINRECOVER)), "domainrecover")
