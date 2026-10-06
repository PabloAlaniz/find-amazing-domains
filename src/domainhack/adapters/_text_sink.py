from __future__ import annotations

from pathlib import Path
from typing import TextIO, TypeAlias

TextTarget: TypeAlias = str | Path | TextIO


class TextSink:
    """A text stream that a file writer may or may not own.

    When built from a path, the file is opened eagerly (so bad paths fail fast)
    and closed by ``close()``. When built from an existing stream, ``close()``
    only flushes it and leaves ownership with the caller.
    """

    def __init__(self, target: TextTarget) -> None:
        if isinstance(target, (str, Path)):
            self.stream: TextIO = open(target, "w", encoding="utf-8", newline="")  # noqa: SIM115
            self._owned = True
        else:
            self.stream = target
            self._owned = False
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owned:
            self.stream.close()
        else:
            self.stream.flush()
