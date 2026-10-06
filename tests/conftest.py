from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.netguard import NetworkGuard


@pytest.fixture(autouse=True)
def network_guard(request: pytest.FixtureRequest) -> Iterator[NetworkGuard | None]:
    """Unit tests must not touch the network; ``integration`` tests are exempt.

    Any attempt raises ``NetworkBlockedError``, and the test also fails at
    teardown in case the code under test swallowed that error.
    """
    if request.node.get_closest_marker("integration") is not None:
        yield None
        return
    guard = NetworkGuard()
    guard.install()
    try:
        yield guard
    finally:
        guard.uninstall()
    if guard.attempts:
        pytest.fail(f"unit test attempted network access: {guard.attempts}", pytrace=False)


@pytest.fixture(autouse=True)
def _isolated_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the default result cache out of the real user cache directory."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
