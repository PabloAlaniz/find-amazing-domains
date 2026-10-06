"""End-to-end runs of ``python -m domainhack`` (network blocked, see ``run_cli``)."""

from tests.fakes import SAMPLES_DIR, run_cli


class TestCLIFilter:
    def test_filter_spanish_sample(self) -> None:
        result = run_cli("--tld", "to", "filter", str(SAMPLES_DIR / "sample_es_5.txt"))
        assert result.returncode == 0
        lines = result.stdout.strip().splitlines()
        assert len(lines) == 20
        assert "plato" not in lines  # plato is not in sample
        assert "abato" in lines

    def test_filter_with_min_length(self) -> None:
        result = run_cli(
            "--tld", "to", "filter", str(SAMPLES_DIR / "sample_es_5.txt"), "--min-length", "6"
        )
        assert result.returncode == 0
        lines = result.stdout.strip().splitlines()
        # All words in sample are 5 letters, so none should pass min-length 6
        assert lines == [] or all(len(w) >= 6 for w in lines)

    def test_filter_english_sample(self) -> None:
        result = run_cli("--tld", "to", "filter", str(SAMPLES_DIR / "sample_en_5.txt"))
        assert result.returncode == 0
        lines = result.stdout.strip().splitlines()
        assert "gusto" in lines
        assert "pesto" in lines


class TestCLICheckDryRun:
    def test_dry_run(self) -> None:
        result = run_cli(
            "--tld", "to", "check", "--file", str(SAMPLES_DIR / "sample_es_5.txt"), "--dry-run"
        )
        assert result.returncode == 0
        assert "aba.to" in result.stdout
        assert "abe.to" in result.stdout

    def test_dry_run_range(self) -> None:
        result = run_cli("--tld", "to", "check", "--range-max", "1", "--dry-run")
        assert result.returncode == 0
        lines = result.stdout.strip().splitlines()
        assert len(lines) == 26  # a-z
        assert "a.to" in lines[0]
        assert "z.to" in lines[-1]

    def test_missing_source_fails(self) -> None:
        result = run_cli("--tld", "to", "check", "--dry-run")
        assert result.returncode != 0
