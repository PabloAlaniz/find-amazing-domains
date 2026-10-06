"""Statuses and expiration in the writers, the console, the summary and the CLI flags."""

import csv
import io
import json
from datetime import datetime, timezone

import pytest

from domainhack.adapters.console_writer import ConsoleResultWriter
from domainhack.adapters.csv_writer import CSV_FIELDS, CsvResultWriter
from domainhack.adapters.json_writer import JsonResultWriter
from domainhack.cli.app import EXIT_OK, build_parser, main
from domainhack.domain.entities import Availability, DomainCheckResult
from domainhack.usecases.check_domains import CheckDomainsUseCase
from tests.fakes import Answer, CollectingWriter, FakeCatalog, ScriptedRegistrar, hack

EXPIRES = datetime(2026, 11, 2, 8, 30, tzinfo=timezone.utc)


def _taken(sld: str = "pla", statuses: tuple[str, ...] = (), expires: datetime | None = None):
    return DomainCheckResult(
        domain=hack(sld),
        availability=Availability.TAKEN,
        statuses=statuses,
        expires_at=expires,
    )


DROPPING = _taken("x", ("server hold", "pending delete"), EXPIRES)


class TestFileWriters:
    def test_csv_columns_are_appended(self) -> None:
        assert CSV_FIELDS[:7] == (
            "fqdn",
            "display",
            "word",
            "sld",
            "tld",
            "availability",
            "error_message",
        )
        assert CSV_FIELDS[7:] == ("statuses", "expires_at")

    def test_csv_row(self) -> None:
        buf = io.StringIO()
        writer = CsvResultWriter(buf)
        writer.write_result(DROPPING)
        writer.write_result(_taken())
        rows = list(csv.DictReader(io.StringIO(buf.getvalue())))
        assert rows[0]["statuses"] == "server hold;pending delete"
        assert rows[0]["expires_at"] == "2026-11-02T08:30:00Z"
        assert rows[1]["statuses"] == ""
        assert rows[1]["expires_at"] == ""

    def test_json_record(self) -> None:
        buf = io.StringIO()
        writer = JsonResultWriter(buf)
        writer.write_result(DROPPING)
        writer.write_result(_taken())
        first, second = (json.loads(line) for line in buf.getvalue().splitlines())
        assert first["statuses"] == ["server hold", "pending delete"]
        assert first["expires_at"] == "2026-11-02T08:30:00Z"
        assert list(first)[-2:] == ["statuses", "expires_at"]
        assert second["statuses"] == []
        assert second["expires_at"] is None


class TestConsole:
    def test_dropping_line_with_show_taken(self, capsys: pytest.CaptureFixture[str]) -> None:
        ConsoleResultWriter(show_taken=True).write_result(DROPPING)
        assert capsys.readouterr().out == (
            "  TAKEN (dropping: pending delete, expires 2026-11-02): x.to\n"
        )

    def test_dropping_line_without_expiry(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = _taken("x", ("redemption period",))
        ConsoleResultWriter(show_dropping=True).write_result(result)
        assert capsys.readouterr().out == "  TAKEN (dropping: redemption period): x.to\n"

    def test_show_dropping_without_show_taken(self, capsys: pytest.CaptureFixture[str]) -> None:
        writer = ConsoleResultWriter(show_dropping=True)
        writer.write_result(_taken("plain", ("active",), EXPIRES))
        writer.write_result(DROPPING)
        assert capsys.readouterr().out == (
            "  TAKEN (dropping: pending delete, expires 2026-11-02): x.to\n"
        )

    def test_dropping_hidden_by_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        ConsoleResultWriter().write_result(DROPPING)
        assert capsys.readouterr().out == ""

    def test_plain_taken_line_is_unchanged(self, capsys: pytest.CaptureFixture[str]) -> None:
        ConsoleResultWriter(show_taken=True).write_result(_taken("pla", ("active",), EXPIRES))
        assert capsys.readouterr().out == "  TAKEN:     pla.to\n"


class TestSummary:
    def test_counts_dropping(self) -> None:
        registrar = ScriptedRegistrar(
            {
                "a": Answer(statuses=("pending delete",)),
                "b": Answer(statuses=("active",)),
                "c": Availability.AVAILABLE,
            }
        )
        summary = CheckDomainsUseCase(registrar, CollectingWriter()).execute(
            [hack("a"), hack("b"), hack("c")]
        )
        assert (summary.available, summary.taken, summary.dropping) == (1, 2, 1)


def _check(*extra: str) -> list[str]:
    return ["check", "--range-max", "1", "--range-end", "c", "--no-progress", "--no-cache", *extra]


class TestCli:
    SCRIPT = {"a": Answer(statuses=("redemption period",), expires_at=EXPIRES)}  # noqa: RUF012

    def test_flag_parsed(self) -> None:
        args = build_parser().parse_args(["check", "--range-max", "1", "--show-dropping"])
        assert args.show_dropping is True

    def test_show_dropping(self, capsys: pytest.CaptureFixture[str]) -> None:
        registrar = ScriptedRegistrar(self.SCRIPT)
        assert main(_check("--show-dropping"), catalog=FakeCatalog(registrar)) == EXIT_OK
        captured = capsys.readouterr()
        assert captured.out == "  TAKEN (dropping: redemption period, expires 2026-11-02): a.to\n"
        assert "Checked 3 domains: 0 available, 3 taken (1 dropping), 0 errors." in captured.err

    def test_show_taken_includes_dropping(self, capsys: pytest.CaptureFixture[str]) -> None:
        registrar = ScriptedRegistrar(self.SCRIPT)
        assert main(_check("--show-taken"), catalog=FakeCatalog(registrar)) == EXIT_OK
        assert capsys.readouterr().out.splitlines() == [
            "  TAKEN (dropping: redemption period, expires 2026-11-02): a.to",
            "  TAKEN:     b.to",
            "  TAKEN:     c.to",
        ]

    def test_summary_without_dropping_is_unchanged(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(_check(), catalog=FakeCatalog(ScriptedRegistrar())) == EXIT_OK
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Checked 3 domains: 0 available, 3 taken, 0 errors." in captured.err
