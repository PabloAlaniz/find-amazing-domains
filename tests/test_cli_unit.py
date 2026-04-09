import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from domainhack.cli.app import (
    _build_domains,
    build_parser,
    cmd_check,
    cmd_filter,
    main,
)
from domainhack.domain.entities import TLD

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"


class TestBuildParser:
    def test_filter_command(self) -> None:
        args = build_parser().parse_args(["filter", "words.txt"])
        assert args.command == "filter"
        assert args.file == Path("words.txt")
        assert args.min_length == 0
        assert args.tld == "to"

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
