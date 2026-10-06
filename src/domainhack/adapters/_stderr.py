"""Write diagnostic lines to stderr without mangling an active progress bar."""

from __future__ import annotations

import sys

from tqdm import tqdm


def write_stderr(message: str) -> None:
    """Print ``message`` on stderr; tqdm clears and redraws a visible bar around it.

    ``sys.stderr`` is looked up on every call so redirection (and pytest's
    capture) is honoured.
    """
    tqdm.write(message, file=sys.stderr)
