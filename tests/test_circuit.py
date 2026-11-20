import threading

import pytest

from domainhack.adapters._circuit import Admission, HostCircuitBreaker
from tests.fakes import FakeClock

HOST = "whois.nic.it"


def _breaker(
    threshold: int = 3, cooldown: float | None = 60.0, max_trials: int = 3
) -> tuple[HostCircuitBreaker, FakeClock, list[str]]:
    clock = FakeClock()
    warnings: list[str] = []
    breaker = HostCircuitBreaker(
        threshold=threshold,
        cooldown=cooldown,
        clock=clock,
        on_open=warnings.append,
        max_trials=max_trials,
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


def test_failed_trial_waits_a_full_cooldown_from_the_failure() -> None:
    # A trial against a dead host can outlast the cooldown (#18): the next
    # trial must still wait a full cooldown after it fails, not go out at once.
    breaker, clock, _ = _breaker()
    _fail(breaker, 3)
    clock.now = 60.0
    assert breaker.admit(HOST) is Admission.TRIAL
    clock.now = 210.0
    breaker.record_failure(HOST)
    clock.now = 269.9
    assert breaker.admit(HOST) is Admission.DENIED
    clock.now = 270.0
    assert breaker.admit(HOST) is Admission.TRIAL


def test_admit_reports_closed_trial_and_denied() -> None:
    breaker, clock, _ = _breaker()
    assert breaker.admit(HOST) is Admission.CLOSED
    _fail(breaker, 3)
    assert breaker.admit(HOST) is Admission.DENIED
    clock.now = 60.0
    assert breaker.admit(HOST) is Admission.TRIAL
    assert breaker.admit(HOST) is Admission.DENIED  # one trial per cooldown
    breaker.record_success(HOST)
    assert breaker.admit(HOST) is Admission.CLOSED


def _fail_trials(breaker: HostCircuitBreaker, clock: FakeClock, times: int) -> None:
    for _ in range(times):
        clock.now += 60.0
        assert breaker.admit(HOST) is Admission.TRIAL
        breaker.record_failure(HOST)


def test_gives_up_after_max_failed_trials() -> None:
    breaker, clock, warnings = _breaker()
    _fail(breaker, 3)
    _fail_trials(breaker, clock, 2)
    assert breaker.dead_hosts() == ()
    assert len(warnings) == 1
    _fail_trials(breaker, clock, 1)
    assert breaker.dead_hosts() == (HOST,)
    assert len(warnings) == 2
    assert warnings[1] == (
        "warning: whois.nic.it still unresponsive after 3 retries; "
        "skipping its remaining domains for the rest of this run"
    )
    clock.now = 1e9
    assert breaker.admit(HOST) is Admission.DENIED
    assert not breaker.allow(HOST)
    assert breaker.skip_message(HOST) == "skipped: whois.nic.it unresponsive (gave up for this run)"
    _fail(breaker, 5)  # late failures do not warn again
    assert len(warnings) == 2


def test_successful_trial_resets_the_trial_count() -> None:
    breaker, clock, _ = _breaker()
    _fail(breaker, 3)
    _fail_trials(breaker, clock, 2)
    clock.now += 60.0
    assert breaker.admit(HOST) is Admission.TRIAL
    breaker.record_success(HOST)
    _fail(breaker, 3)
    _fail_trials(breaker, clock, 2)
    assert breaker.dead_hosts() == ()


def test_failure_in_flight_when_the_circuit_opened_is_not_a_trial() -> None:
    breaker, clock, warnings = _breaker(max_trials=1)
    _fail(breaker, 4)  # the fourth was already in flight
    assert breaker.dead_hosts() == ()
    clock.now = 59.0
    assert breaker.admit(HOST) is Admission.DENIED
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


def test_invalid_max_trials() -> None:
    with pytest.raises(ValueError):
        HostCircuitBreaker(max_trials=0)


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
