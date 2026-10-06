import argparse
from unittest.mock import MagicMock, patch

from domainhack.adapters.tqdm_progress import TqdmProgressReporter
from domainhack.cli.app import _build_progress, _progress_total, build_parser, cmd_check
from domainhack.ports.progress import NullProgressReporter


class TestNoProgressFlag:
    def test_default_shows_progress(self) -> None:
        args = build_parser().parse_args(["check", "--range-max", "1"])
        assert args.no_progress is False
        assert isinstance(_build_progress(args), TqdmProgressReporter)

    def test_flag_disables_progress(self) -> None:
        args = build_parser().parse_args(["check", "--range-max", "1", "--no-progress"])
        assert args.no_progress is True
        assert isinstance(_build_progress(args), NullProgressReporter)


class TestProgressTotal:
    def test_range_mode_is_exact(self) -> None:
        args = build_parser().parse_args(["check", "--range-max", "2"])
        assert _progress_total(args) == 26 + 26 * 26

    def test_range_mode_respects_end(self) -> None:
        args = build_parser().parse_args(["check", "--range-max", "2", "--range-end", "ac"])
        assert _progress_total(args) == 29

    def test_file_mode_is_indeterminate(self) -> None:
        args = build_parser().parse_args(["check", "--file", "w.txt"])
        assert _progress_total(args) is None


class TestCmdCheckWiresProgress:
    def test_passes_reporter_and_total(self) -> None:
        mock_registrar = MagicMock()
        mock_registrar.__enter__ = MagicMock(return_value=mock_registrar)
        mock_registrar.__exit__ = MagicMock(return_value=False)
        args = argparse.Namespace(
            tld="to",
            file=None,
            range_max=1,
            range_end="c",
            dry_run=False,
            delay=0.0,
            show_taken=False,
            no_progress=True,
        )

        with (
            patch("domainhack.cli.app.build_registrar_for", return_value=mock_registrar),
            patch("domainhack.cli.app.ConsoleResultWriter"),
            patch("domainhack.cli.app.CheckDomainsUseCase") as mock_uc_cls,
        ):
            cmd_check(args)

        progress = mock_uc_cls.call_args.args[2]
        assert isinstance(progress, NullProgressReporter)
        assert mock_uc_cls.return_value.execute.call_args.kwargs["total"] == 3
