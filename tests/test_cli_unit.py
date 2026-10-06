import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from domainhack.adapters.composite_writer import CompositeResultWriter
from domainhack.adapters.console_writer import ConsoleResultWriter
from domainhack.adapters.csv_writer import CsvResultWriter
from domainhack.adapters.json_writer import JsonResultWriter
from domainhack.cli.app import (
    OutputFormatError,
    _build_domains,
    _build_writer,
    _resolve_output_format,
    build_parser,
    cmd_check,
    cmd_filter,
    main,
)
from domainhack.domain.entities import TLD, Availability, DomainCheckResult

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"


class TestBuildParser:
    def test_filter_command(self) -> None:
        args = build_parser().parse_args(["filter", "words.txt"])
        assert args.command == "filter"
        assert args.file == Path("words.txt")
        assert args.min_length == 0
        assert args.tld == [TLD("to")]

    def test_check_file_command(self) -> None:
        args = build_parser().parse_args(["check", "--file", "w.txt"])
        assert args.command == "check"
        assert args.file == Path("w.txt")
        assert args.delay == 1.0
        assert args.show_taken is False
        assert args.dry_run is False

    def test_check_range_command(self) -> None:
        args = build_parser().parse_args(["check", "--range-max", "3", "--dry-run"])
        assert args.range_max == 3
        assert args.dry_run is True
        assert args.file is None

    def test_check_requires_source(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["check", "--dry-run"])


class TestBuildDomains:
    def test_file_mode(self) -> None:
        args = argparse.Namespace(
            file=SAMPLES_DIR / "sample_es_5.txt",
            tld="to",
            range_max=None,
            range_end=None,
        )
        tld = TLD("to")
        domains = list(_build_domains(args, tld))
        words = [d.word for d in domains]
        assert "abato" in words
        assert len(domains) == 20

    def test_range_mode(self) -> None:
        args = argparse.Namespace(
            file=None,
            range_max=1,
            range_end=None,
        )
        tld = TLD("to")
        domains = list(_build_domains(args, tld))
        assert len(domains) == 26
        assert domains[0].fqdn == "a.to"
        assert domains[25].fqdn == "z.to"


class TestCmdFilter:
    def test_prints_words(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = argparse.Namespace(
            tld="to",
            file=SAMPLES_DIR / "sample_es_5.txt",
            min_length=0,
        )
        cmd_filter(args)
        output = capsys.readouterr().out
        assert "abato" in output
        assert "abeto" in output


class TestCmdCheck:
    def test_dry_run_prints_domains(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = argparse.Namespace(
            tld="to",
            file=SAMPLES_DIR / "sample_es_5.txt",
            dry_run=True,
            delay=0.0,
            show_taken=False,
            range_max=None,
            range_end=None,
        )
        cmd_check(args)
        output = capsys.readouterr().out
        assert "aba.to" in output
        assert "abe.to" in output

    def test_live_uses_registrar(self) -> None:
        mock_registrar = MagicMock()
        mock_registrar.__enter__ = MagicMock(return_value=mock_registrar)
        mock_registrar.__exit__ = MagicMock(return_value=False)

        args = argparse.Namespace(
            tld="to",
            file=None,
            range_max=1,
            range_end="b",
            dry_run=False,
            delay=0.0,
            show_taken=False,
        )

        with (
            patch("domainhack.cli.app.TonicRegistrarClient", return_value=mock_registrar),
            patch("domainhack.cli.app.ConsoleResultWriter"),
            patch("domainhack.cli.app.CheckDomainsUseCase") as mock_uc_cls,
        ):
            cmd_check(args)
            mock_uc_cls.return_value.execute.assert_called_once()


class TestMain:
    def test_dispatches_filter(self) -> None:
        with (
            patch("sys.argv", ["domainhack", "filter", str(SAMPLES_DIR / "sample_es_5.txt")]),
            patch("domainhack.cli.app.cmd_filter") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()

    def test_dispatches_check(self) -> None:
        with (
            patch("sys.argv", ["domainhack", "check", "--range-max", "1", "--dry-run"]),
            patch("domainhack.cli.app.cmd_check") as mock_cmd,
        ):
            main()
            mock_cmd.assert_called_once()


class TestOutputOptions:
    def test_defaults_to_no_output(self) -> None:
        args = build_parser().parse_args(["check", "--range-max", "1"])
        assert args.output is None
        assert args.format is None

    def test_parses_output_and_format(self) -> None:
        args = build_parser().parse_args(
            ["check", "--range-max", "1", "--output", "r.txt", "--format", "csv"]
        )
        assert args.output == Path("r.txt")
        assert args.format == "csv"

    def test_rejects_unknown_format(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["check", "--range-max", "1", "--format", "xml"])

    @pytest.mark.parametrize(
        ("name", "expected"),
        [("r.csv", "csv"), ("r.CSV", "csv"), ("r.json", "json"), ("r.jsonl", "json")],
    )
    def test_infers_format_from_extension(self, name: str, expected: str) -> None:
        assert _resolve_output_format(Path(name), None) == expected

    def test_explicit_format_overrides_extension(self) -> None:
        assert _resolve_output_format(Path("r.csv"), "json") == "json"

    def test_unknown_extension_without_format_errors(self) -> None:
        with pytest.raises(OutputFormatError, match="Cannot infer"):
            _resolve_output_format(Path("results.txt"), None)


class TestBuildWriter:
    def _args(self, **kwargs: object) -> argparse.Namespace:
        base: dict[str, object] = {"show_taken": False, "output": None, "format": None}
        base.update(kwargs)
        return argparse.Namespace(**base)

    def test_console_only_without_output(self) -> None:
        assert isinstance(_build_writer(self._args()), ConsoleResultWriter)

    def test_composite_with_csv(self, tmp_path: Path) -> None:
        writer = _build_writer(self._args(output=tmp_path / "r.csv"))
        assert isinstance(writer, CompositeResultWriter)
        assert any(isinstance(w, CsvResultWriter) for w in writer._writers)
        writer.flush()

    def test_composite_with_json(self, tmp_path: Path) -> None:
        writer = _build_writer(self._args(output=tmp_path / "r.out", format="json"))
        assert isinstance(writer, CompositeResultWriter)
        assert any(isinstance(w, JsonResultWriter) for w in writer._writers)
        writer.flush()


class TestCmdCheckOutput:
    def _args(self, output: Path, fmt: str | None = None) -> argparse.Namespace:
        return argparse.Namespace(
            tld="to",
            file=None,
            range_max=1,
            range_end="b",
            dry_run=False,
            delay=0.0,
            show_taken=False,
            output=output,
            format=fmt,
        )

    def _fake_registrar(self) -> MagicMock:
        registrar = MagicMock()
        registrar.__enter__ = MagicMock(return_value=registrar)
        registrar.__exit__ = MagicMock(return_value=False)
        registrar.check_availability.side_effect = lambda d: DomainCheckResult(
            domain=d, availability=Availability.AVAILABLE
        )
        return registrar

    def test_writes_csv_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        out = tmp_path / "results.csv"
        with patch("domainhack.cli.app.TonicRegistrarClient", return_value=self._fake_registrar()):
            cmd_check(self._args(out))
        lines = out.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "fqdn,word,sld,tld,availability,error_message"
        assert lines[1:] == ["a.to,ato,a,to,available,", "b.to,bto,b,to,available,"]
        # Console output is still produced alongside the file.
        assert "AVAILABLE: a.to" in capsys.readouterr().out

    def test_writes_jsonl_file(self, tmp_path: Path) -> None:
        out = tmp_path / "results.jsonl"
        with patch("domainhack.cli.app.TonicRegistrarClient", return_value=self._fake_registrar()):
            cmd_check(self._args(out))
        records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        assert [r["fqdn"] for r in records] == ["a.to", "b.to"]

    def test_bad_extension_fails_before_network(self, tmp_path: Path) -> None:
        with (
            patch("domainhack.cli.app.TonicRegistrarClient") as mock_client,
            pytest.raises(OutputFormatError),
        ):
            cmd_check(self._args(tmp_path / "results.txt"))
        mock_client.assert_not_called()

    def test_main_reports_bad_extension_as_usage_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = ["domainhack", "check", "--range-max", "1", "--output", str(tmp_path / "r.txt")]
        with (
            patch("sys.argv", argv),
            patch("domainhack.cli.app.TonicRegistrarClient") as mock_client,
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 2
        assert "Cannot infer output format" in capsys.readouterr().err
        mock_client.assert_not_called()
