import io
import json
from pathlib import Path
from typing import Any

from domainhack.adapters.json_writer import JsonResultWriter
from domainhack.domain.entities import TLD, Availability, DomainCheckResult, DomainHack


def _result(availability: Availability, word: str = "plato", error: str = "") -> DomainCheckResult:
    hack = DomainHack.from_word(word, TLD("to"))
    assert hack is not None
    return DomainCheckResult(domain=hack, availability=availability, error_message=error)


def _read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class TestJsonResultWriter:
    def test_empty_run_produces_empty_file(self, tmp_path: Path) -> None:
        out = tmp_path / "out.jsonl"
        JsonResultWriter(out).flush()
        assert out.read_text(encoding="utf-8") == ""

    def test_writes_one_object_per_line(self, tmp_path: Path) -> None:
        out = tmp_path / "out.jsonl"
        writer = JsonResultWriter(out)
        writer.write_result(_result(Availability.AVAILABLE))
        writer.write_result(_result(Availability.ERROR, "abeto", "timeout"))
        writer.flush()

        assert _read_lines(out) == [
            {
                "fqdn": "pla.to",
                "word": "plato",
                "sld": "pla",
                "tld": "to",
                "availability": "available",
                "error_message": "",
            },
            {
                "fqdn": "abe.to",
                "word": "abeto",
                "sld": "abe",
                "tld": "to",
                "availability": "error",
                "error_message": "timeout",
            },
        ]

    def test_lines_are_streamed_before_flush(self, tmp_path: Path) -> None:
        out = tmp_path / "out.jsonl"
        writer = JsonResultWriter(out)
        writer.write_result(_result(Availability.TAKEN))
        assert [r["fqdn"] for r in _read_lines(out)] == ["pla.to"]
        writer.flush()

    def test_preserves_non_ascii(self, tmp_path: Path) -> None:
        out = tmp_path / "out.jsonl"
        writer = JsonResultWriter(out)
        writer.write_result(_result(Availability.AVAILABLE, "ñato"))
        writer.flush()
        assert "ñato" in out.read_text(encoding="utf-8")
        assert _read_lines(out)[0]["sld"] == "ña"

    def test_flush_closes_owned_file_and_is_idempotent(self, tmp_path: Path) -> None:
        writer = JsonResultWriter(tmp_path / "out.jsonl")
        writer.flush()
        writer.flush()
        assert writer._sink.stream.closed

    def test_accepts_text_stream_without_closing_it(self) -> None:
        buf = io.StringIO()
        writer = JsonResultWriter(buf)
        writer.write_result(_result(Availability.AVAILABLE))
        writer.flush()
        assert not buf.closed
        assert json.loads(buf.getvalue())["fqdn"] == "pla.to"
