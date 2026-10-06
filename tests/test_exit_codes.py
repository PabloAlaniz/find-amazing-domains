"""Exit statuses, clean error messages, Ctrl-C handling and the stdout/stderr split."""

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from domainhack.cli.app import (
    EXIT_FAILURE,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_USAGE,
    build_parser,
    main,
)
from domainhack.domain.entities import Availability, DomainCheckResult, DomainHack
from domainhack.ports.registrar import RegistrarClient

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"

Outcome = Callable[[DomainHack], DomainCheckResult]


def _result(domain: DomainHack, availability: Availability) -> DomainCheckResult:
    message = "boom" if availability is Availability.ERROR else ""
    return DomainCheckResult(domain=domain, availability=availability, error_message=message)


class ScriptedRegistrar(RegistrarClient):
    """Answers by SLD; an SLD mapped to an exception class raises it instead."""

    def __init__(self, script: dict[str, Availability | type[BaseException]]) -> None:
        self._script = script
        self.calls: list[str] = []
        self.closed = False

    def check_availability(self, domain: DomainHack) -> DomainCheckResult:
        self.calls.append(domain.fqdn)
        outcome = self._script.get(domain.sld, Availability.TAKEN)
        if isinstance(outcome, Availability):
            return _result(domain, outcome)
        raise outcome()

    def close(self) -> None:
        self.closed = True


def _run(
    argv: list[str], registrar: RegistrarClient | None
) -> tuple[int, ScriptedRegistrar | None]:
    with patch("domainhack.cli.app.build_registrar_for", return_value=registrar):
        code = main(argv)
    return code, registrar if isinstance(registrar, ScriptedRegistrar) else None


def _check(*extra: str) -> list[str]:
    return ["check", "--range-max", "1", "--range-end", "c", "--no-progress", "--no-cache", *extra]


def _assert_one_clean_error(err: str, needle: str) -> None:
    assert "Traceback" not in err
    lines = err.strip().splitlines()
    assert len(lines) == 1, err
    assert lines[0].startswith("error: ")
    assert needle in lines[0]


class TestCompletedRuns:
    def test_all_ok_exits_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        registrar = ScriptedRegistrar({"a": Availability.AVAILABLE})
        code, _ = _run(_check(), registrar)
        assert code == EXIT_OK
        captured = capsys.readouterr()
        assert captured.out == "  AVAILABLE: a.to (word: 'ato')\n"
        assert "Done. Checked 3 domains: 1 available, 2 taken, 0 errors." in captured.err

    def test_some_errors_exit_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        registrar = ScriptedRegistrar({"a": Availability.AVAILABLE, "b": Availability.ERROR})
        code, _ = _run(_check(), registrar)
        assert code == EXIT_FAILURE
        assert "1 errors" in capsys.readouterr().err

    def test_all_errors_exit_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        script = dict.fromkeys("abc", Availability.ERROR)
        code, _ = _run(_check(), ScriptedRegistrar(script))
        assert code == EXIT_FAILURE
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.count("ERROR:") == 3


class TestStdoutStderrSplit:
    def test_stdout_has_results_only(self, capsys: pytest.CaptureFixture[str]) -> None:
        script = {"a": Availability.AVAILABLE, "b": Availability.ERROR, "c": Availability.TAKEN}
        _run(_check("--show-taken"), ScriptedRegistrar(script))
        captured = capsys.readouterr()
        assert captured.out.splitlines() == [
            "  AVAILABLE: a.to (word: 'ato')",
            "  TAKEN:     c.to",
        ]
        err_lines = [line for line in captured.err.splitlines() if line.strip()]
        assert err_lines[0] == "  ERROR:     b.to -- boom"
        assert err_lines[-1].startswith("Done. Checked 3 domains")

    def test_dry_run_is_pipeable(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "domainhack", "check", "--range-max", "1", "--dry-run"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == EXIT_OK
        assert len(result.stdout.splitlines()) == 26
        assert result.stderr == ""


class TestInterrupt:
    def test_ctrl_c_flushes_csv_and_exits_130(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "partial.csv"
        script: dict[str, Availability | type[BaseException]] = {
            "a": Availability.AVAILABLE,
            "c": KeyboardInterrupt,
        }
        registrar = ScriptedRegistrar(script)
        code, _ = _run(_check("--output", str(out)), registrar)

        assert code == EXIT_INTERRUPTED
        assert registrar.calls == ["a.to", "b.to", "c.to"]
        assert registrar.closed
        assert out.read_text(encoding="utf-8").splitlines() == [
            "fqdn,display,word,sld,tld,availability,error_message",
            "a.to,a.to,ato,a,to,available,",
            "b.to,b.to,bto,b,to,taken,",
        ]
        captured = capsys.readouterr()
        assert "Done" not in captured.out + captured.err
        assert "Traceback" not in captured.err
        assert "Interrupted after 2 checks (1 available, 0 errors)." in captured.err
        assert captured.out == "  AVAILABLE: a.to (word: 'ato')\n"

    def test_ctrl_c_outside_the_check_loop(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("domainhack.cli.app.build_registrar_for", side_effect=KeyboardInterrupt):
            assert main(_check()) == EXIT_INTERRUPTED
        assert capsys.readouterr().err == "Interrupted.\n"

    def test_ctrl_c_during_filter(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("domainhack.cli.app.cmd_filter", side_effect=KeyboardInterrupt):
            assert main(["filter", "words.txt"]) == EXIT_INTERRUPTED
        assert "Traceback" not in capsys.readouterr().err


class TestRuntimeErrors:
    def test_missing_word_file(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["filter", "/nonexistent/words.txt"]) == EXIT_FAILURE
        _assert_one_clean_error(capsys.readouterr().err, "No such file or directory")

    def test_missing_word_file_in_check_fails_before_network(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = ["check", "--file", "/nonexistent/words.txt", "--no-progress"]
        with patch("domainhack.cli.app.build_registrar_for") as catalog:
            assert main(argv) == EXIT_FAILURE
        catalog.assert_not_called()
        _assert_one_clean_error(capsys.readouterr().err, "cannot read word list")

    @pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX non-root")
    def test_unreadable_word_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        words = tmp_path / "words.txt"
        words.write_text("plato\n")
        words.chmod(0)
        try:
            assert main(["filter", str(words)]) == EXIT_FAILURE
        finally:
            words.chmod(0o600)
        _assert_one_clean_error(capsys.readouterr().err, "Permission denied")

    def test_latin1_word_list_is_a_clear_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        words = tmp_path / "es.txt"
        words.write_bytes("mañana\nplato\n".encode("latin-1"))
        assert main(["filter", str(words)]) == EXIT_FAILURE
        _assert_one_clean_error(capsys.readouterr().err, "not valid utf-8 (byte 0xf1)")

    def test_latin1_word_list_with_encoding(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        words = tmp_path / "es.txt"
        words.write_bytes("piñait\nbandit\n".encode("latin-1"))
        args = ["--tld", "it", "filter", "--encoding", "latin-1", str(words)]
        assert main(args) == EXIT_OK
        assert capsys.readouterr().out.splitlines() == ["piñait", "bandit"]

    def test_bad_output_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        out = tmp_path / "missing" / "r.csv"
        registrar = ScriptedRegistrar({})
        code, _ = _run(_check("--output", str(out)), registrar)
        assert code == EXIT_FAILURE
        assert registrar.calls == []
        _assert_one_clean_error(capsys.readouterr().err, "cannot write output file")

    def test_unsupported_tlds_only(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, _ = _run(["--tld", "in", *_check()], None)
        assert code == EXIT_FAILURE
        _assert_one_clean_error(capsys.readouterr().err, "no registrar supports .in")

    def test_unexpected_os_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        error = PermissionError(13, "Permission denied", "/some/file")
        with patch("domainhack.cli.app.cmd_filter", side_effect=error):
            assert main(["filter", "x.txt"]) == EXIT_FAILURE
        assert capsys.readouterr().err == "error: /some/file: Permission denied\n"

    def test_broken_pipe(self) -> None:
        with (
            patch("domainhack.cli.app.cmd_filter", side_effect=BrokenPipeError),
            patch("domainhack.cli.app._silence_stdout") as silence,
        ):
            assert main(["filter", "x.txt"]) == EXIT_FAILURE
        silence.assert_called_once()

    def test_broken_pipe_subprocess_is_quiet(self) -> None:
        """``domainhack ... | head -1``: no traceback, no 'Exception ignored' noise."""
        proc = subprocess.Popen(
            [sys.executable, "-m", "domainhack", "check", "--range-max", "3", "--dry-run"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdout is not None and proc.stderr is not None
        assert proc.stdout.readline().strip().startswith(b"a.to")
        proc.stdout.close()
        stderr = proc.stderr.read()
        proc.stderr.close()
        assert proc.wait(timeout=30) == EXIT_FAILURE
        assert stderr == b""


class TestCacheFallback:
    def test_corrupt_cache_warns_and_continues(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cache = tmp_path / "results.sqlite3"
        cache.write_bytes(b"not a database" * 200)
        argv = [
            "check",
            "--range-max",
            "1",
            "--range-end",
            "b",
            "--no-progress",
            "--cache-path",
            str(cache),
        ]
        registrar = ScriptedRegistrar({"a": Availability.AVAILABLE})
        code, _ = _run(argv, registrar)
        assert code == EXIT_OK
        assert registrar.calls == ["a.to", "b.to"]
        captured = capsys.readouterr()
        assert captured.err.count("warning: result cache disabled") == 1
        assert "Traceback" not in captured.err
        assert "AVAILABLE: a.to" in captured.out

    def test_directory_as_cache_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        argv = ["check", "--range-max", "1", "--range-end", "a", "--no-progress"]
        registrar = ScriptedRegistrar({})
        code, _ = _run([*argv, "--cache-path", str(tmp_path)], registrar)
        assert code == EXIT_OK
        assert capsys.readouterr().err.count("warning: result cache disabled") == 1


class TestUsageErrors:
    @pytest.mark.parametrize(
        "argv",
        [
            ["check", "--range-max", "7"],
            ["check", "--range-max", "0"],
            ["check", "--range-max", "x"],
            ["check", "--range-max", "1", "--delay", "-1"],
            ["check", "--range-max", "1", "--delay", "inf"],
            ["check", "--range-max", "1", "--cache-ttl", "-5"],
            ["check", "--range-max", "1", "--cache-ttl", "nan"],
            ["check", "--range-max", "2", "--range-end", "A1"],
            ["check", "--range-max", "1", "--range-end", "ab"],
            ["check", "--file", "w.txt", "--range-end", "ab"],
            ["check", "--range-max", "1", "--encoding", "klingon"],
            ["check", "--range-max", "1", "--contact", "not-an-email"],
            ["check", "--range-max", "1", "--contact", "a@b.c\r\nX-Injected: 1"],
            ["check", "--range-max", "1", "--delay", "fast"],
            ["filter", "w.txt", "--min-length", "-1"],
            ["filter", "w.txt", "--min-length", "x"],
            ["--tld", "to,1", "filter", "w.txt"],
            ["check"],
        ],
    )
    def test_exit_2(self, argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
        with patch("domainhack.cli.app.build_registrar_for") as catalog:
            assert main(argv) == EXIT_USAGE
        catalog.assert_not_called()
        err = capsys.readouterr().err
        assert "usage:" in err
        assert "Traceback" not in err

    def test_contact_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOMAINHACK_CONTACT", "me@example.com")
        args = build_parser().parse_args(["check", "--range-max", "1"])
        assert args.contact == "me@example.com"

    def test_help_documents_exit_codes(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["--help"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "exit status:" in out
        for code in ("0", "1", "2", "130"):
            assert f"  {code} " in out

    def test_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["--version"]) == EXIT_OK
        assert capsys.readouterr().out.startswith("domainhack ")


class TestSubprocessExitCodes:
    """Through ``python -m domainhack``, i.e. the real interpreter exit path."""

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "domainhack", *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_missing_file_is_one_line_exit_1(self) -> None:
        result = self._run("filter", "/nonexistent/words.txt")
        assert result.returncode == EXIT_FAILURE
        assert result.stdout == ""
        _assert_one_clean_error(result.stderr, "cannot read word list")

    def test_latin1_is_one_line_exit_1(self, tmp_path: Path) -> None:
        words = tmp_path / "es.txt"
        words.write_bytes("mañana\n".encode("latin-1"))
        result = self._run("filter", str(words))
        assert result.returncode == EXIT_FAILURE
        _assert_one_clean_error(result.stderr, "--encoding")

    def test_range_max_7_is_usage_error(self) -> None:
        result = self._run("check", "--range-max", "7")
        assert result.returncode == EXIT_USAGE
        assert "must be between 1 and 6" in result.stderr
        assert "Traceback" not in result.stderr

    def test_bad_output_dir_exit_1(self, tmp_path: Path) -> None:
        # .to resolves from the built-in RDAP overrides: no network before the failure.
        out = tmp_path / "missing" / "r.csv"
        result = self._run("check", "--range-max", "1", "--no-progress", "--output", str(out))
        assert result.returncode == EXIT_FAILURE
        _assert_one_clean_error(result.stderr, "cannot write output file")
