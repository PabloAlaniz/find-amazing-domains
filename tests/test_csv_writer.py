import csv
import io
from pathlib import Path

from domainhack.adapters.csv_writer import CSV_FIELDS, CsvResultWriter
from domainhack.domain.entities import TLD, Availability, DomainCheckResult, DomainHack


def _result(
    availability: Availability, word: str = "plato", error: str = "", tld: str = "to"
) -> DomainCheckResult:
    hack = DomainHack.from_word(word, TLD(tld))
    assert hack is not None
    return DomainCheckResult(domain=hack, availability=availability, error_message=error)


EMPTY_DETAILS = {
    "registered_at": "",
    "registrar": "",
    "nameservers": "",
    "parked_hint": "",
    "dns_nameservers": "",
    "dns_conflict": "false",
}


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class TestCsvResultWriter:
    def test_writes_header_even_without_results(self, tmp_path: Path) -> None:
        out = tmp_path / "out.csv"
        CsvResultWriter(out).flush()
        assert out.read_text(encoding="utf-8").splitlines() == [",".join(CSV_FIELDS)]

    def test_writes_one_row_per_result(self, tmp_path: Path) -> None:
        out = tmp_path / "out.csv"
        writer = CsvResultWriter(out)
        writer.write_result(_result(Availability.AVAILABLE))
        writer.write_result(_result(Availability.TAKEN, "grato"))
        writer.write_result(_result(Availability.ERROR, "abeto", "boom, timeout"))
        writer.flush()

        rows = _read_rows(out)
        assert rows == [
            {
                "fqdn": "pla.to",
                "display": "pla.to",
                "word": "plato",
                "sld": "pla",
                "tld": "to",
                "availability": "available",
                "error_message": "",
                "statuses": "",
                "expires_at": "",
                **EMPTY_DETAILS,
            },
            {
                "fqdn": "gra.to",
                "display": "gra.to",
                "word": "grato",
                "sld": "gra",
                "tld": "to",
                "availability": "taken",
                "error_message": "",
                "statuses": "",
                "expires_at": "",
                **EMPTY_DETAILS,
            },
            {
                "fqdn": "abe.to",
                "display": "abe.to",
                "word": "abeto",
                "sld": "abe",
                "tld": "to",
                "availability": "error",
                "error_message": "boom, timeout",
                "statuses": "",
                "expires_at": "",
                **EMPTY_DETAILS,
            },
        ]

    def test_rows_are_streamed_before_flush(self, tmp_path: Path) -> None:
        out = tmp_path / "out.csv"
        writer = CsvResultWriter(out)
        writer.write_result(_result(Availability.AVAILABLE))
        # Simulates an interrupted run: data must already be on disk.
        rows = _read_rows(out)
        assert [r["fqdn"] for r in rows] == ["pla.to"]
        writer.flush()

    def test_flush_closes_owned_file_and_is_idempotent(self, tmp_path: Path) -> None:
        writer = CsvResultWriter(tmp_path / "out.csv")
        writer.flush()
        writer.flush()
        assert writer._sink.stream.closed

    def test_accepts_str_path(self, tmp_path: Path) -> None:
        out = tmp_path / "out.csv"
        writer = CsvResultWriter(str(out))
        writer.write_result(_result(Availability.AVAILABLE))
        writer.flush()
        assert len(_read_rows(out)) == 1

    def test_accepts_text_stream_without_closing_it(self) -> None:
        buf = io.StringIO()
        writer = CsvResultWriter(buf)
        writer.write_result(_result(Availability.AVAILABLE))
        writer.flush()
        assert not buf.closed
        lines = buf.getvalue().splitlines()
        assert lines[0] == ",".join(CSV_FIELDS)
        assert lines[1] == "pla.to,pla.to,plato,pla,to,available,,,,,,,,,false"
