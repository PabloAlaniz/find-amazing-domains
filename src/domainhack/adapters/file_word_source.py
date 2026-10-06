from collections.abc import Iterator
from pathlib import Path

from domainhack.ports.word_source import WordSource

DEFAULT_ENCODING = "utf-8"


class WordListError(Exception):
    """The word list cannot be read; the message is meant for the end user."""


class FileWordSource(WordSource):
    """Reads words line-by-line from a text file.

    Decoding is strict: a byte that is not valid in ``encoding`` raises
    ``WordListError`` instead of being replaced, because a mis-decoded word
    (e.g. ``ma\\ufffdana``) would turn into a wrong domain to query.
    """

    def __init__(self, file_path: Path, encoding: str = DEFAULT_ENCODING) -> None:
        self._file_path = file_path
        self._encoding = encoding

    def check_readable(self) -> None:
        """Fail fast (``WordListError``) if the file cannot be opened at all."""
        try:
            with open(self._file_path, "rb"):
                pass
        except OSError as exc:
            raise self._os_error(exc) from exc

    def _os_error(self, exc: OSError) -> WordListError:
        reason = exc.strerror or str(exc)
        return WordListError(f"cannot read word list '{self._file_path}': {reason}")

    def words(self) -> Iterator[str]:
        try:
            with open(self._file_path, encoding=self._encoding) as f:
                for line in f:
                    word = line.strip()
                    if word:
                        yield word
        except UnicodeDecodeError as exc:
            bad = exc.object[exc.start]
            raise WordListError(
                f"cannot read word list '{self._file_path}': not valid {self._encoding} "
                f"(byte {bad:#04x}); convert it to UTF-8 or pass --encoding (e.g. latin-1)"
            ) from exc
        except OSError as exc:
            raise self._os_error(exc) from exc
