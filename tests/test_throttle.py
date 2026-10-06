import threading

import pytest

from domainhack.adapters._throttle import (
    DEFAULT_MAX_INTERVAL,
    MIN_PENALTY_INTERVAL,
    HostThrottle,
    full_jitter_backoff,
)
from tests.fakes import FakeClock, FakeRandom, fake_throttle

HOST = "rdap.example.test"


class TestSpacing:
    def test_first_request_does_not_wait_then_base_delay(self) -> None:
        clock = FakeClock()
        throttle = fake_throttle(clock)
        throttle.wait(HOST, 1.0)
        throttle.wait(HOST, 1.0)
        assert clock.sleeps == [1.0]

    def test_hosts_are_independent(self) -> None:
        clock = FakeClock()
        throttle = fake_throttle(clock)
        throttle.wait("a", 1.0)
        throttle.wait("b", 1.0)
        assert clock.sleeps == []

    @pytest.mark.parametrize(("value", "expected"), [(0.0, 0.8), (0.5, 1.0), (0.75, 1.1)])
    def test_jitter_is_plus_minus_20_percent(self, value: float, expected: float) -> None:
        clock = FakeClock()
        throttle = fake_throttle(clock, FakeRandom(value))
        throttle.wait(HOST, 1.0)
        throttle.wait(HOST, 1.0)
        assert clock.sleeps == [pytest.approx(expected)]

    def test_jitter_draws_from_the_injected_source(self) -> None:
        clock = FakeClock()
        random = FakeRandom(0.0, 1.0)
        throttle = fake_throttle(clock, random)
        for _ in range(3):
            throttle.wait(HOST, 10.0)
        assert clock.sleeps == [pytest.approx(8.0), pytest.approx(12.0)]
        assert random.calls == 3

    def test_zero_delay_draws_no_jitter(self) -> None:
        clock = FakeClock()
        random = FakeRandom()
        throttle = fake_throttle(clock, random)
        throttle.wait(HOST, 0.0)
        throttle.wait(HOST, 0.0)
        assert clock.sleeps == []
        assert random.calls == 0

    def test_jitter_can_be_disabled(self) -> None:
        clock = FakeClock()
        throttle = fake_throttle(clock, FakeRandom(0.0), jitter=0.0)
        throttle.wait(HOST, 1.0)
        throttle.wait(HOST, 1.0)
        assert clock.sleeps == [1.0]


class TestSlowDownAndRecovery:
    def test_slow_down_doubles_from_the_base(self) -> None:
        clock = FakeClock()
        throttle = fake_throttle(clock)
        throttle.wait(HOST, 1.5)
        assert throttle.slow_down(HOST) == 3.0
        assert throttle.slow_down(HOST) == 6.0
        assert throttle.interval(HOST) == 6.0

    def test_slow_down_is_capped(self) -> None:
        clock = FakeClock()
        throttle = fake_throttle(clock)
        throttle.wait(HOST, 1.0)
        for _ in range(10):
            throttle.slow_down(HOST)
        assert throttle.interval(HOST) == DEFAULT_MAX_INTERVAL

    def test_custom_cap_and_factor(self) -> None:
        throttle = fake_throttle(FakeClock(), backoff_factor=3.0, max_interval=5.0)
        throttle.wait(HOST, 1.0)
        assert throttle.slow_down(HOST) == 3.0
        assert throttle.slow_down(HOST) == 5.0

    def test_slowing_a_zero_delay_host_still_spaces_it(self) -> None:
        throttle = fake_throttle(FakeClock())
        throttle.wait(HOST, 0.0)
        assert throttle.slow_down(HOST) == MIN_PENALTY_INTERVAL
        assert throttle.slow_down(HOST) == 2 * MIN_PENALTY_INTERVAL

    def test_slow_down_takes_effect_immediately(self) -> None:
        clock = FakeClock()
        throttle = fake_throttle(clock)
        throttle.wait(HOST, 1.0)  # t=0, next slot t=1
        throttle.slow_down(HOST)  # interval 2: next request not before t=2
        throttle.wait(HOST, 1.0)
        assert clock.sleeps == [2.0]
        throttle.wait(HOST, 1.0)  # spaced by the slowed-down interval
        assert clock.sleeps == [2.0, 2.0]

    def test_success_recovers_gradually_down_to_the_base(self) -> None:
        throttle = fake_throttle(FakeClock())
        throttle.wait(HOST, 1.0)
        throttle.slow_down(HOST)
        throttle.slow_down(HOST)  # 4.0
        intervals = []
        for _ in range(15):
            throttle.record_success(HOST)
            intervals.append(throttle.interval(HOST))
        assert intervals[:3] == pytest.approx([3.6, 3.24, 2.916])
        assert intervals == sorted(intervals, reverse=True)
        assert intervals[-1] == 1.0  # back at the base, never below it

    def test_success_without_slow_down_is_a_no_op(self) -> None:
        throttle = fake_throttle(FakeClock())
        throttle.wait(HOST, 1.0)
        throttle.record_success(HOST)
        assert throttle.interval(HOST) == 1.0

    def test_interval_for_an_explicit_base(self) -> None:
        throttle = fake_throttle(FakeClock())
        assert throttle.interval(HOST) == 0.0
        assert throttle.interval(HOST, 4.0) == 4.0

    @pytest.mark.parametrize(
        "kwargs",
        [{"jitter": 1.0}, {"jitter": -0.1}, {"backoff_factor": 0.5}, {"recovery": 0.0}],
    )
    def test_rejects_bad_parameters(self, kwargs: dict[str, float]) -> None:
        with pytest.raises(ValueError):
            HostThrottle(**kwargs)  # type: ignore[arg-type]


class TestDefer:
    def test_defer_holds_the_next_request_back(self) -> None:
        clock = FakeClock()
        throttle = fake_throttle(clock)
        throttle.wait(HOST, 1.0)
        throttle.defer(HOST, 5.0)
        throttle.wait(HOST, 1.0)
        assert clock.sleeps == [5.0]

    def test_defer_never_shortens_a_pending_wait(self) -> None:
        clock = FakeClock()
        throttle = fake_throttle(clock)
        throttle.wait(HOST, 10.0)
        throttle.defer(HOST, 2.0)
        throttle.wait(HOST, 10.0)
        assert clock.sleeps == [10.0]

    def test_slow_down_and_defer_overlap_instead_of_adding_up(self) -> None:
        clock = FakeClock()
        throttle = fake_throttle(clock)
        throttle.wait(HOST, 1.0)
        throttle.slow_down(HOST)  # 2 s
        throttle.defer(HOST, 3.0)
        throttle.wait(HOST, 1.0)
        assert clock.sleeps == [3.0]  # max(2, 3), not 2 + 3


class TestFullJitterBackoff:
    @pytest.mark.parametrize(
        ("attempt", "value", "expected"),
        [(0, 0.5, 1.0), (1, 0.5, 2.0), (2, 0.5, 4.0), (0, 0.0, 0.0), (10, 0.5, 15.0)],
    )
    def test_formula(self, attempt: int, value: float, expected: float) -> None:
        # random(0, min(cap=30, base=2 * 2**attempt))
        assert full_jitter_backoff(attempt, FakeRandom(value)) == expected

    def test_custom_base_and_cap(self) -> None:
        assert full_jitter_backoff(3, FakeRandom(1.0), base=1.0, cap=5.0) == 5.0


def test_thread_safe_slots_are_never_shared() -> None:
    """Concurrent callers each get their own slot: total sleep is 0+1+...+(n-1)."""
    lock = threading.Lock()
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        with lock:
            sleeps.append(seconds)

    throttle = HostThrottle(clock=lambda: 0.0, sleep=sleep, random=FakeRandom())
    threads = [threading.Thread(target=throttle.wait, args=(HOST, 1.0)) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(sleeps) == [float(n) for n in range(1, 20)]
