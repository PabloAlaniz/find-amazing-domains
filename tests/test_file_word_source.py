from pathlib import Path

from domainhack.adapters.file_word_source import FileWordSource


class TestFileWordSource:
    def test_reads_words_from_file(self, tmp_path: Path) -> None:
        word_file = tmp_path / "words.txt"
        word_file.write_text("plato\ngrato\nabeto\n", encoding="utf-8")

        source = FileWordSource(word_file)
        words = list(source.words())
        assert words == ["plato", "grato", "abeto"]

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        word_file = tmp_path / "words.txt"
        word_file.write_text("plato\n\n\ngrato\n  \nabeto\n", encoding="utf-8")

        source = FileWordSource(word_file)
        words = list(source.words())
        assert words == ["plato", "grato", "abeto"]

    def test_strips_whitespace(self, tmp_path: Path) -> None:
        word_file = tmp_path / "words.txt"
        word_file.write_text("  plato  \n  grato  \n", encoding="utf-8")

        source = FileWordSource(word_file)
        words = list(source.words())
        assert words == ["plato", "grato"]

    def test_empty_file(self, tmp_path: Path) -> None:
        word_file = tmp_path / "words.txt"
        word_file.write_text("", encoding="utf-8")

        source = FileWordSource(word_file)
        words = list(source.words())
        assert words == []

    def test_streams_lazily(self, tmp_path: Path) -> None:
        word_file = tmp_path / "words.txt"
        word_file.write_text("plato\ngrato\nabeto\n", encoding="utf-8")

        source = FileWordSource(word_file)
        gen = source.words()
        assert next(gen) == "plato"
        assert next(gen) == "grato"
