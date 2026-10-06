import threading

import pytest

from domainhack.adapters._circuit import HostCircuitBreaker

HOST = "whois.nic.it"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _breaker(
    threshold: int = 3, cooldown: float | None = 60.0
) -> tuple[HostCircuitBreaker, FakeClock, list[str]]:
    clock = FakeClock()
    warnings: list[str] = []
    breaker = HostCircuitBreaker(
        threshold=threshold, cooldown=cooldown, clock=clock, on_open=warnings.append
    )
    return breaker, clock, warnings


def _fail(breaker: HostCircuitBreaker, times: int, host: str = HOST) -> None:
    for _ in range(times):
        breaker.record_failure(host)


def test_closed_until_threshold_consecutive_failures() -> None:
    breaker, _, warnings = _breaker()
    _fail(breaker, 2)
    assert breaker.allow(HOST)
    assert not breaker.is_open(HOST)
    _fail(breaker, 1)
    assert breaker.is_open(HOST)
    assert not breaker.allow(HOST)
    assert len(warnings) == 1
    assert HOST in warnings[0]
    assert "circuit open" in warnings[0]


def test_success_resets_the_counter() -> None:
    breaker, _, warnings = _breaker()
    _fail(breaker, 2)
    breaker.record_success(HOST)
    _fail(breaker, 2)
    assert breaker.allow(HOST)
    assert warnings == []


def test_hosts_are_independent() -> None:
    breaker, _, _ = _breaker()
    _fail(breaker, 3)
    assert not breaker.allow(HOST)
    assert breaker.allow("rdap.identitydigital.services")


def test_further_failures_while_open_do_not_warn_again() -> None:
    breaker, _, warnings = _breaker()
    _fail(breaker, 10)
    assert len(warnings) == 1


def test_half_open_allows_one_trial_after_cooldown() -> None:
    breaker, clock, _ = _breaker(cooldown=60.0)
    _fail(breaker, 3)
    clock.now = 59.9
    assert not breaker.allow(HOST)
    clock.now = 60.0
    assert breaker.allow(HOST)  # the trial
    assert not breaker.allow(HOST)  # others still skipped while it runs


def test_successful_trial_closes_the_circuit() -> None:
    breaker, clock, warnings = _breaker()
    _fail(breaker, 3)
    clock.now = 60.0
    assert breaker.allow(HOST)
    breaker.record_success(HOST)
    assert not breaker.is_open(HOST)
    assert breaker.allow(HOST)
    assert breaker.allow(HOST)
    # It takes a fresh run of failures to open it again, with a new warning.
    _fail(breaker, 2)
    assert breaker.allow(HOST)
    _fail(breaker, 1)
    assert not breaker.allow(HOST)
    assert len(warnings) == 2


def test_failed_trial_reopens_for_another_cooldown() -> None:
    breaker, clock, warnings = _breaker()
    _fail(breaker, 3)
    clock.now = 60.0
    assert breaker.allow(HOST)
    breaker.record_failure(HOST)
    assert breaker.is_open(HOST)
    clock.now = 119.0
    assert not breaker.allow(HOST)
    clock.now = 120.0
    assert breaker.allow(HOST)
    assert len(warnings) == 1


def test_no_cooldown_stays_open() -> None:
    breaker, clock, warnings = _breaker(cooldown=None)
    _fail(breaker, 3)
    clock.now = 1e9
    assert not breaker.allow(HOST)
    assert "not retrying" in warnings[0]


def test_skip_message() -> None:
    breaker, _, _ = _breaker()
    assert breaker.skip_message(HOST) == "skipped: whois.nic.it unresponsive (circuit open)"


def test_invalid_threshold() -> None:
    with pytest.raises(ValueError):
        HostCircuitBreaker(threshold=0)


def test_default_warning_goes_to_stderr_once(capsys: pytest.CaptureFixture[str]) -> None:
    breaker = HostCircuitBreaker(threshold=3, clock=FakeClock())
    _fail(breaker, 5)
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("warning: whois.nic.it unresponsive")
    assert "retrying in 60s" in lines[0]


def test_concurrent_failures_open_once() -> None:
    breaker, _, warnings = _breaker(threshold=3)
    threads = [threading.Thread(target=_fail, args=(breaker, 50)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert breaker.is_open(HOST)
    assert len(warnings) == 1
