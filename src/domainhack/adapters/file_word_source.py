from collections.abc import Iterator
from pathlib import Path

from domainhack.ports.word_source import WordSource


class FileWordSource(WordSource):
    """Reads words line-by-line from a text file."""

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path

    def words(self) -> Iterator[str]:
        with open(self._file_path, encoding="utf-8") as f:
            for line in f:
                word = line.strip()
                if word:
                    yield word
