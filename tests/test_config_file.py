"""The configuration file: where it is found, what it may hold, and what it changes."""

from pathlib import Path

import pytest

from domainhack.adapters.config_file import (
    Config,
    ConfigError,
    default_config_paths,
    find_config,
    load_config,
)
from domainhack.cli.app import EXIT_OK, EXIT_USAGE, build_parser, config_from_argv, main

FULL = """
[run]
delay = 2
parallel = 8
contact = "me@example.com"
cache_path = "cache.sqlite3"

[presets]
Mine = ["COM", "dev", "com.ar"]
everything = "catalog"

[name]
tlds = "mine,io"
prefixes = ["get", "try"]
suffixes = ["hq"]

[check]
frequency_list = ["en.txt", "/abs/es.txt"]
"""


def _write(tmp_path: Path, text: str, name: str = "c.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestLoad:
    def test_no_file_is_an_empty_config(self) -> None:
        assert load_config(None) == Config()

    def test_full_file(self, tmp_path: Path) -> None:
        path = _write(tmp_path, FULL)
        config = load_config(path)
        assert config.path == path
        assert config.run == {
            "delay": 2.0,
            "parallel": 8,
            "contact": "me@example.com",
            "cache_path": tmp_path.resolve() / "cache.sqlite3",
        }
        assert isinstance(config.run["delay"], float)
        assert config.presets == {"mine": ["com", "dev", "com.ar"], "everything": "catalog"}
        assert config.name_tlds == "mine,io"
        assert config.name_prefixes == ("get", "try")
        assert config.name_suffixes == ("hq",)
        assert config.frequency_lists == (tmp_path.resolve() / "en.txt", Path("/abs/es.txt"))

    def test_a_single_frequency_list_may_be_a_string(self, tmp_path: Path) -> None:
        config = load_config(_write(tmp_path, '[check]\nfrequency_list = "en.txt"\n'))
        assert config.frequency_lists == (tmp_path.resolve() / "en.txt",)

    def test_home_is_expanded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        config = load_config(_write(tmp_path, '[run]\ncache_path = "~/c.sqlite3"\n'))
        assert config.run["cache_path"] == tmp_path / "home" / "c.sqlite3"

    @pytest.mark.parametrize(
        ("text", "fragment"),
        [
            ("x = 1", "'x' must be a table"),
            ("[run", "not valid TOML"),
            ("[runs]\ndelay = 1", "unknown table [runs]"),
            ("[run]\ndelai = 1", "unknown key 'delai' in [run]"),
            ("[run]\ndelay = -1", "[run] delay: expected a number >= 0"),
            ('[run]\ndelay = "1"', "[run] delay"),
            ("[run]\ndelay = true", "[run] delay"),
            ("[run]\ndelay = nan", "[run] delay"),
            ("[run]\nparallel = 0", "[run] parallel: expected an integer >= 1"),
            ("[run]\nparallel = 1.5", "[run] parallel"),
            ('[run]\ncontact = ""', "[run] contact"),
            ("[run]\ncache_path = 3", "[run] cache_path"),
            ('[presets]\nmine = "com"', "[presets] preset 'mine'"),
            ("[presets]\nmine = [1]", "[presets] preset 'mine'"),
            ("[name]\ntlds = []", "[name] tlds"),
            ('[name]\nprefixes = "get"', "[name] prefixes"),
            ('[name]\nprefixes = ["a","b","c","d","e","f","g","h","i","j","k"]', "at most 10"),
            ("[check]\nfrequency_list = [1]", "[check] frequency_list"),
        ],
    )
    def test_bad_files_are_refused_with_the_place(
        self, tmp_path: Path, text: str, fragment: str
    ) -> None:
        path = _write(tmp_path, text)
        with pytest.raises(ConfigError) as info:
            load_config(path)
        assert fragment in str(info.value)
        assert str(path) in str(info.value)

    def test_unreadable_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="cannot read"):
            load_config(tmp_path)  # a directory

    def test_contact_uses_the_callers_check(self, tmp_path: Path) -> None:
        path = _write(tmp_path, '[run]\ncontact = "nope"\n')
        assert load_config(path).run["contact"] == "nope"

        def refuse(value: str) -> str:
            raise TypeError(f"not an email address: {value!r}")

        with pytest.raises(ConfigError, match="not an email address"):
            load_config(path, contact=refuse)


class TestFind:
    def test_nothing_by_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert find_config() is None

    def test_explicit_wins_then_env_then_local_then_xdg(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        xdg = tmp_path / "xdg-config" / "domainhack" / "config.toml"
        xdg.parent.mkdir(parents=True)
        xdg.write_text("")
        assert default_config_paths()[1] == xdg
        assert find_config() == xdg
        local = _write(tmp_path, "", "domainhack.toml")
        assert find_config() == Path(local.name)
        env = _write(tmp_path, "", "env.toml")
        monkeypatch.setenv("DOMAINHACK_CONFIG", str(env))
        assert find_config() == env
        explicit = _write(tmp_path, "", "explicit.toml")
        assert find_config(explicit) == explicit

    def test_xdg_defaults_to_dot_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("XDG_CONFIG_HOME")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert default_config_paths()[1] == tmp_path / ".config" / "domainhack" / "config.toml"

    def test_a_named_file_must_exist(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ConfigError, match="not found"):
            find_config(tmp_path / "nope.toml")
        monkeypatch.setenv("DOMAINHACK_CONFIG", str(tmp_path / "nope.toml"))
        with pytest.raises(ConfigError, match="not found"):
            find_config()


class TestCli:
    def test_file_values_become_defaults_and_the_command_line_wins(self, tmp_path: Path) -> None:
        path = _write(tmp_path, FULL)
        argv = ["--config", str(path), "check", "--range-max", "1"]
        args = build_parser(config_from_argv(argv)).parse_args(argv)
        assert (args.delay, args.parallel, args.contact) == (2.0, 8, "me@example.com")
        assert args.cache_path == tmp_path.resolve() / "cache.sqlite3"
        argv += ["--delay", "5", "--contact", "you@example.com"]
        args = build_parser(config_from_argv(argv)).parse_args(argv)
        assert (args.delay, args.parallel, args.contact) == (5.0, 8, "you@example.com")

    def test_option_works_after_the_command_too(self, tmp_path: Path) -> None:
        path = _write(tmp_path, FULL)
        argv = ["name", "acme", "--config", str(path)]
        args = build_parser(config_from_argv(argv)).parse_args(argv)
        assert (args.delay, args.tlds) == (2.0, "mine,io")
        assert args.config_data.presets["mine"] == ["com", "dev", "com.ar"]

    def test_environment_contact_wins_over_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = _write(tmp_path, FULL)
        monkeypatch.setenv("DOMAINHACK_CONTACT", "env@example.com")
        argv = ["--config", str(path), "check", "--range-max", "1"]
        assert build_parser(config_from_argv(argv)).parse_args(argv).contact == "env@example.com"

    def test_no_config_ignores_every_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DOMAINHACK_CONFIG", str(_write(tmp_path, FULL)))
        assert config_from_argv(["check", "--range-max", "1"]).run["delay"] == 2.0
        assert config_from_argv(["--no-config", "check", "--range-max", "1"]) == Config()
        assert config_from_argv(["check", "--range-max", "1", "--no-config"]) == Config()

    def test_help_shows_the_file_defaults(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = _write(tmp_path, FULL)
        assert main(["--config", str(path), "name", "--help"]) == EXIT_OK
        out = " ".join(capsys.readouterr().out.split())
        assert "Seconds between requests (default: 2.0)" in out
        assert "all-supported, mine, everything" in out

    @pytest.mark.parametrize("text", ["[nope]", '[run]\ncontact = "not-an-email"'])
    def test_a_bad_file_is_a_usage_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str
    ) -> None:
        path = _write(tmp_path, text)
        assert main(["--config", str(path), "tlds"]) == EXIT_USAGE
        assert f"error: {path}: " in capsys.readouterr().err

    def test_a_missing_named_file_is_a_usage_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["--config", str(tmp_path / "nope.toml"), "tlds"]) == EXIT_USAGE
        assert "configuration file not found" in capsys.readouterr().err

    def test_local_file_is_picked_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, "[check]\nfrequency_list = 'freq.txt'\n", "domainhack.toml")
        (tmp_path / "freq.txt").write_text("gusto\nplato\n")
        (tmp_path / "words.txt").write_text("veto\nplato\ngusto\n")
        code = main(["check", "--file", "words.txt", "--order", "frequency", "--dry-run"])
        assert code == EXIT_OK
        assert [ln.split()[0] for ln in capsys.readouterr().out.splitlines()] == [
            "gus.to",
            "pla.to",
            "ve.to",
        ]
