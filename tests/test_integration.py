"""Integration tests that hit the real Tonic.to registrar.

Run manually with: pytest -m integration -v
Excluded from CI by default via pyproject.toml addopts.
"""

from pathlib import Path

import pytest

from domainhack.adapters.console_writer import ConsoleResultWriter
from domainhack.adapters.file_word_source import FileWordSource
from domainhack.adapters.tonic_registrar import TonicRegistrarClient
from domainhack.cli.app import cmd_check
from domainhack.domain.entities import TLD, Availability, DomainHack
from domainhack.usecases.check_domains import CheckDomainsUseCase
from domainhack.usecases.filter_words import FilterWordsUseCase
from domainhack.usecases.generate_range import RangeWordSource

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"

pytestmark = pytest.mark.integration


class TestBruteForceRealCheck:
    def test_checks_domains_and_prints_summary(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Brute-force a.to + b.to against the real registrar."""
        tld = TLD("to")
        source = RangeWordSource(max_length=1, end_at="b")
        domains = [DomainHack.from_sld(sld, tld) for sld in source.words()]

        with TonicRegistrarClient(delay=1.5) as registrar:
            writer = ConsoleResultWriter(show_taken=True)
            CheckDomainsUseCase(registrar, writer).execute(domains)

        output = capsys.readouterr().out
        assert "a.to" in output
        assert "b.to" in output
        assert "Done. Checked 2 domains" in output


class TestWordlistRealCheck:
    def test_dry_run_wordlist_pipeline(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Filter sample file and dry-run — full pipeline without HTTP."""
        source = FileWordSource(SAMPLES_DIR / "sample_es_5.txt")
        tld = TLD("to")
        domains = list(FilterWordsUseCase(source, tld).execute())

        assert len(domains) == 20
        for domain in domains:
            print(f"  {domain.fqdn}  (word: {domain.word!r})")

        output = capsys.readouterr().out
        assert "aba.to" in output


class TestFilterThenCheckPipeline:
    def test_filter_output_feeds_into_check(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Step 1: filter words
        source = FileWordSource(SAMPLES_DIR / "sample_es_5.txt")
        tld = TLD("to")
        hacks = list(FilterWordsUseCase(source, tld).execute())
        assert len(hacks) > 0

        # Step 2: write filtered words to temp file
        word_file = tmp_path / "filtered.txt"
        word_file.write_text("\n".join(h.word for h in hacks) + "\n")

        # Step 3: dry-run check on filtered file — uses _build_domains
        import argparse

        args = argparse.Namespace(
            tld="to",
            file=word_file,
            range_max=None,
            range_end=None,
            dry_run=True,
            delay=0.0,
            show_taken=False,
        )
        cmd_check(args)

        output = capsys.readouterr().out
        assert ".to" in output


class TestTonicRegistrarRealRequest:
    def test_known_taken_domain(self) -> None:
        """'google.to' should be taken."""
        hack = DomainHack.from_sld("google", TLD("to"))
        with TonicRegistrarClient(delay=0.0) as client:
            result = client.check_availability(hack)
        assert result.availability == Availability.TAKEN

    def test_likely_available_domain(self) -> None:
        """A random gibberish SLD is likely available."""
        hack = DomainHack.from_sld("xqzjvkw", TLD("to"))
        with TonicRegistrarClient(delay=0.0) as client:
            result = client.check_availability(hack)
        # Can't guarantee availability, but can guarantee no ERROR
        assert result.availability != Availability.ERROR
