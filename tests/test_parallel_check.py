"""Parallel checks: one lane per registry host, at most one request in flight per host.

Every wait here ends on a threading.Event/Barrier (or fails after
``WAIT_TIMEOUT``): no sleeps, no network.
"""

from __future__ import annotations

import _thread
import argparse
import queue
import signal
import subprocess
import sys
import threading
from collections.abc import Hashable, Iterator
from pathlib import Path

import httpx
import pytest

from domainhack.adapters.pacing import lane_for
from domainhack.adapters.rdap_registrar import RdapRegistrarClient
from domainhack.adapters.registrar_router import RegistrarRouter
from domainhack.adapters.whois_registrar import WHOIS_SERVERS, WhoisRegistrarClient
from domainhack.cli import app
from domainhack.cli.app import EXIT_INTERRUPTED, EXIT_OK, EXIT_USAGE, build_parser, main
from domainhack.domain.entities import TLD, Availability, DomainCheckResult, DomainHack
from domainhack.ports.progress import ProgressReporter
from domainhack.ports.registrar import RegistrarClient
from domainhack.usecases import check_domains, lanes
from domainhack.usecases.check_domains import (
    DEFAULT_PARALLEL,
    CheckDomainsUseCase,
    CheckSummary,
    LaneKey,
)
from domainhack.usecases.lanes import LaneOutcome, LaneScheduler, unexpected_error
from tests.fakes import (
    WAIT_TIMEOUT,
    CollectingWriter,
    ConcurrencyProbe,
    FakeCatalog,
    FakeClock,
    FakeResultCache,
    GatedRegistrar,
    ScriptedRegistrar,
    TESTS_DIR,
    SignallingWriter,
    guarded_env,
    hack,
)

HOSTS = {"to": "rdap.tonic", "io": "rdap.id", "sh": "rdap.id", "in": "rdap.in"}


def by_host(domain: DomainHack) -> Hashable:
    return HOSTS[domain.tld.suffix]


def router_for(registrars: dict[str, RegistrarClient]) -> RegistrarRouter:
    return RegistrarRouter(lambda tld: registrars.get(tld.suffix))


def domains(*fqdns: str) -> list[DomainHack]:
    return [hack(*fqdn.split(".")) for fqdn in fqdns]


class Background:
    """Runs ``execute`` on another thread so the test can open gates meanwhile."""

    def __init__(self, use_case: CheckDomainsUseCase, items: object) -> None:
        self.summary: CheckSummary | None = None
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, args=(use_case, items), daemon=True)
        self.thread.start()

    def _run(self, use_case: CheckDomainsUseCase, items: object) -> None:
        try:
            self.summary = use_case.execute(items)  # type: ignore[arg-type]
        except BaseException as exc:
            self.error = exc

    def join(self) -> CheckSummary:
        self.thread.join(WAIT_TIMEOUT)
        assert not self.thread.is_alive(), "execute() hung"
        if self.error is not None:
            raise self.error
        assert self.summary is not None
        return self.summary


class TestConcurrency:
    def test_two_hosts_run_concurrently(self) -> None:
        probe = ConcurrencyProbe()
        # Both checks must be inside the registrar at the same time to pass the barrier.
        barrier = threading.Barrier(2)
        to = GatedRegistrar("rdap.tonic", probe, barrier=barrier)
        io = GatedRegistrar("rdap.id", probe, barrier=barrier)
        writer = CollectingWriter()
        uc = CheckDomainsUseCase(
            router_for({"to": to, "io": io}), writer, parallel=2, lane_key=by_host
        )
        summary = uc.execute(domains("a.to", "a.io"))
        assert summary == CheckSummary(taken=2)
        assert probe.max_total == 2
        assert len(probe.threads) == 2

    def test_same_host_is_never_concurrent(self) -> None:
        probe = ConcurrencyProbe()
        gate = threading.Event()
        # .io and .sh share a host (one lane); .to is on another.
        io = GatedRegistrar("rdap.id", probe, gates={"a.io": gate})
        sh = GatedRegistrar("rdap.id", probe)
        to = GatedRegistrar("rdap.tonic", probe, default=Availability.AVAILABLE)
        writer = SignallingWriter()
        uc = CheckDomainsUseCase(
            router_for({"io": io, "sh": sh, "to": to}), writer, parallel=4, lane_key=by_host
        )
        run = Background(uc, domains("a.io", "a.to", "a.sh", "b.to", "b.io", "c.to"))
        try:
            # The .to lane finishes while a.io is held open...
            assert writer.wait_for(3)
            assert writer.fqdns == ["a.to", "b.to", "c.to"]
            # ...and nothing else on a.io's host has started.
            assert [f for f in probe.started if not f.endswith(".to")] == ["a.io"]
        finally:
            gate.set()
        summary = run.join()
        assert summary == CheckSummary(available=3, taken=3)
        assert writer.fqdns[3:] == ["a.io", "a.sh", "b.io"]  # ranked order within the lane
        assert probe.max_in_flight == {"rdap.id": 1, "rdap.tonic": 1}

    def test_many_hosts_respect_both_caps(self) -> None:
        probe = ConcurrencyProbe()
        suffixes = ["to", "io", "sh", "in"]
        registrars: dict[str, RegistrarClient] = {
            s: GatedRegistrar(HOSTS[s], probe) for s in suffixes
        }
        items = [hack(f"w{i}", s) for i in range(25) for s in suffixes]
        writer = CollectingWriter()
        uc = CheckDomainsUseCase(
            router_for(registrars), writer, parallel=2, lane_key=by_host, lane_capacity=3
        )
        summary = uc.execute(items)
        assert summary.checked == len(items)
        assert sorted(r.domain.fqdn for r in writer.results) == sorted(d.fqdn for d in items)
        assert all(n == 1 for n in probe.max_in_flight.values())
        assert probe.max_total <= 2
        assert len(probe.threads) == 2
        for host in set(HOSTS.values()):
            lane = [d.fqdn for d in items if HOSTS[d.tld.suffix] == host]
            assert [f for f in probe.started if f in lane] == lane

    def test_single_lane_starts_a_single_worker(self) -> None:
        probe = ConcurrencyProbe()
        to = GatedRegistrar("rdap.tonic", probe)
        uc = CheckDomainsUseCase(to, CollectingWriter(), parallel=8, lane_key=by_host)
        assert uc.execute(domains("a.to", "b.to", "c.to")).taken == 3
        assert probe.started == ["a.to", "b.to", "c.to"]
        assert len(probe.threads) == 1

    def test_default_lane_key_is_one_lane(self) -> None:
        probe = ConcurrencyProbe()
        registrar = GatedRegistrar("any", probe)
        uc = CheckDomainsUseCase(registrar, CollectingWriter(), parallel=4)
        uc.execute(domains("a.to", "a.io", "b.to"))
        assert probe.max_total == 1
        assert probe.started == ["a.to", "a.io", "b.to"]


class TestSequential:
    def test_parallel_1_is_the_old_sequential_loop(self) -> None:
        probe = ConcurrencyProbe()
        to = GatedRegistrar("rdap.tonic", probe, {"b": Availability.AVAILABLE})
        io = GatedRegistrar("rdap.id", probe)
        writer = CollectingWriter()
        items = domains("a.to", "a.io", "b.to", "b.io")
        summary = CheckDomainsUseCase(
            router_for({"to": to, "io": io}), writer, parallel=1, lane_key=by_host
        ).execute(items)
        assert [r.domain for r in writer.results] == items
        assert probe.started == [d.fqdn for d in items]
        assert probe.threads == {threading.current_thread().name}
        assert summary == CheckSummary(available=1, taken=3)

    def test_parallel_1_still_raises_unexpected_errors(self) -> None:
        registrar = ScriptedRegistrar({"a": RuntimeError})
        writer = CollectingWriter()
        with pytest.raises(RuntimeError):
            CheckDomainsUseCase(registrar, writer).execute(domains("a.to"))
        assert writer.flushed

    def test_sequential_uses_the_cache(self) -> None:
        registrar = ScriptedRegistrar()
        cache = FakeResultCache({"a.to": Availability.AVAILABLE})
        writer = CollectingWriter()
        CheckDomainsUseCase(registrar, writer, cache=cache).execute(domains("a.to", "b.to"))
        assert registrar.calls == ["b.to"]
        assert cache.stored == ["b.to"]
        assert [r.raw_title for r in writer.results] == ["cache", "live"]

    def test_parallel_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="parallel"):
            CheckDomainsUseCase(ScriptedRegistrar(), CollectingWriter(), parallel=0)


class TestCache:
    def test_cache_hits_bypass_the_lanes(self) -> None:
        probe = ConcurrencyProbe()
        gate = threading.Event()
        to = GatedRegistrar("rdap.tonic", probe, gates={"a.to": gate})
        cache = FakeResultCache({"b.to": Availability.TAKEN, "c.to": Availability.AVAILABLE})
        writer = SignallingWriter()
        uc = CheckDomainsUseCase(to, writer, cache=cache, parallel=2, lane_key=by_host)
        run = Background(uc, domains("a.to", "b.to", "c.to", "d.to"))
        try:
            # Same lane as the held a.to, yet both hits are written right away.
            assert writer.wait_for(2)
            assert writer.fqdns == ["b.to", "c.to"]
            assert probe.started == ["a.to"]
        finally:
            gate.set()
        summary = run.join()
        assert summary == CheckSummary(available=1, taken=3)
        assert to.calls == ["a.to", "d.to"]
        assert cache.lookups == ["a.to", "b.to", "c.to", "d.to"]
        assert cache.stored == ["a.to", "d.to"]
        # The cache is only ever used from the thread running execute().
        assert cache.threads == {run.thread.name}


class TestOrdering:
    def _run(self, *, keep_order: bool) -> list[str]:
        probe = ConcurrencyProbe()
        gate = threading.Event()
        to = GatedRegistrar("rdap.tonic", probe, gates={"a.to": gate})
        io = GatedRegistrar("rdap.id", probe)
        writer = SignallingWriter()
        uc = CheckDomainsUseCase(
            router_for({"to": to, "io": io}),
            writer,
            parallel=2,
            lane_key=by_host,
            keep_order=keep_order,
        )
        run = Background(uc, domains("a.to", "a.io", "b.io", "b.to"))
        try:
            assert io.started("b.io").wait(WAIT_TIMEOUT)
            if not keep_order:
                assert writer.wait_for(2)
        finally:
            gate.set()
        run.join()
        return writer.fqdns

    def test_results_come_in_completion_order(self) -> None:
        assert self._run(keep_order=False) == ["a.io", "b.io", "a.to", "b.to"]

    def test_keep_order_restores_input_order(self) -> None:
        assert self._run(keep_order=True) == ["a.to", "a.io", "b.io", "b.to"]

    def test_keep_order_with_cache_hits(self) -> None:
        cache = FakeResultCache({"b.to": Availability.AVAILABLE})
        writer = CollectingWriter()
        uc = CheckDomainsUseCase(
            ScriptedRegistrar(), writer, cache=cache, parallel=3, keep_order=True
        )
        uc.execute(domains("a.to", "b.to", "c.to"))
        assert [r.domain.fqdn for r in writer.results] == ["a.to", "b.to", "c.to"]

    def test_keep_order_holds_a_bounded_number_of_results(self) -> None:
        probe = ConcurrencyProbe()
        gate = threading.Event()
        to = GatedRegistrar("rdap.tonic", probe, gates={"a.to": gate})
        io = GatedRegistrar("rdap.id", probe)
        consumed = 0
        bound_reached = threading.Event()
        # lane_capacity 1 x parallel 2 x 4 = 8 results held while a.to is open.
        max_held = 8

        def source() -> Iterator[DomainHack]:
            nonlocal consumed
            yield hack("a", "to")
            for i in range(100):
                consumed += 1
                # held (max_held + 1) + in flight and waiting on .io (2)
                if consumed > max_held + 3 and not gate.is_set():
                    raise AssertionError(f"consumed {consumed} domains while results were held")
                if consumed == max_held + 1:
                    bound_reached.set()
                yield hack(f"x{i}", "io")

        writer = CollectingWriter()
        uc = CheckDomainsUseCase(
            router_for({"to": to, "io": io}),
            writer,
            parallel=2,
            lane_key=by_host,
            keep_order=True,
            lane_capacity=1,
        )
        run = Background(uc, source())
        try:
            assert bound_reached.wait(WAIT_TIMEOUT)
        finally:
            gate.set()
        assert run.join().checked == 101
        assert writer.results[0].domain.fqdn == "a.to"


class TestErrorsAndInterrupts:
    def test_worker_exception_is_reported_as_error(self) -> None:
        class Kaput(Exception):
            def __init__(self) -> None:
                super().__init__("kaput")

        to = ScriptedRegistrar({"b": Kaput, "c": Availability.AVAILABLE})
        writer = CollectingWriter()
        summary = CheckDomainsUseCase(to, writer, parallel=2).execute(
            domains("a.to", "b.to", "c.to")
        )
        assert summary == CheckSummary(available=1, taken=1, errors=1)
        (error,) = [r for r in writer.results if r.availability is Availability.ERROR]
        assert error.domain.fqdn == "b.to"
        assert error.error_message == "unexpected error: Kaput: kaput"

    def test_unexpected_error_without_message(self) -> None:
        result = unexpected_error(hack("a"), RuntimeError())
        assert result.error_message == "unexpected error: RuntimeError"

    def test_keyboard_interrupt_in_a_worker_interrupts_the_run(self) -> None:
        to = ScriptedRegistrar({"c": KeyboardInterrupt})
        writer = CollectingWriter()
        summary = CheckDomainsUseCase(to, writer, parallel=2, shutdown_grace=0).execute(
            domains("a.to", "b.to", "c.to", "d.to")
        )
        assert summary == CheckSummary(taken=2, interrupted=True)
        assert to.calls == ["a.to", "b.to", "c.to"]
        assert writer.flushed

    def test_other_base_exceptions_propagate(self) -> None:
        to = ScriptedRegistrar({"a": SystemExit})
        writer = CollectingWriter()
        with pytest.raises(SystemExit):
            CheckDomainsUseCase(to, writer, parallel=2).execute(domains("a.to", "b.to"))
        assert writer.flushed

    def test_ctrl_c_in_the_source_keeps_finished_results(self) -> None:
        probe = ConcurrencyProbe()
        to = GatedRegistrar("rdap.tonic", probe)

        def source() -> Iterator[DomainHack]:
            yield hack("a")
            # Interrupt only once a.to has been checked.
            assert to.started("a.to").wait(WAIT_TIMEOUT)
            yield hack("b")
            raise KeyboardInterrupt

        writer = CollectingWriter()
        summary = CheckDomainsUseCase(
            to, writer, parallel=2, lane_key=by_host, shutdown_grace=WAIT_TIMEOUT
        ).execute(source())
        assert summary.interrupted
        # a.to was in flight (or done): the grace period lets it be written.
        assert "a.to" in [r.domain.fqdn for r in writer.results]
        assert writer.flushed

    def test_ctrl_c_abandons_a_stuck_request(self) -> None:
        probe = ConcurrencyProbe()
        gate = threading.Event()
        to = GatedRegistrar("rdap.tonic", probe, gates={"a.to": gate})

        def source() -> Iterator[DomainHack]:
            yield hack("a")
            assert to.started("a.to").wait(WAIT_TIMEOUT)
            raise KeyboardInterrupt

        writer = CollectingWriter()
        try:
            summary = CheckDomainsUseCase(
                to, writer, parallel=2, shutdown_grace=0.01
            ).execute(source())
        finally:
            gate.set()
        assert summary == CheckSummary(interrupted=True)
        assert writer.flushed

    def test_keep_order_writes_held_results_on_interrupt(self) -> None:
        probe = ConcurrencyProbe()
        gate = threading.Event()
        to = GatedRegistrar("rdap.tonic", probe, gates={"a.to": gate})
        io = GatedRegistrar("rdap.id", probe)

        def source() -> Iterator[DomainHack]:
            yield hack("a", "to")
            yield hack("a", "io")
            assert io.started("a.io").wait(WAIT_TIMEOUT)
            raise KeyboardInterrupt

        writer = CollectingWriter()
        try:
            summary = CheckDomainsUseCase(
                router_for({"to": to, "io": io}),
                writer,
                parallel=2,
                lane_key=by_host,
                keep_order=True,
                shutdown_grace=0.2,
            ).execute(source())
        finally:
            gate.set()
        assert summary.interrupted
        # a.io finished but sat behind the stuck a.to: it is written anyway.
        assert [r.domain.fqdn for r in writer.results] == ["a.io"]


class TestLaziness:
    def test_lazy_source_is_consumed_in_bounded_steps(self) -> None:
        """A huge range-mode generator is never read ahead of the lane bound."""
        probe = ConcurrencyProbe()
        gate = threading.Event()
        to = GatedRegistrar("rdap.tonic", probe, gates={"a0.to": gate})
        capacity = 4
        # in flight (1) + waiting (capacity) + the one that did not fit (1)
        bound = capacity + 2
        consumed = 0
        bound_reached = threading.Event()

        def source() -> Iterator[DomainHack]:
            nonlocal consumed
            for i in range(10_000):
                consumed += 1
                if consumed > bound and not gate.is_set():
                    raise AssertionError(f"read {consumed} domains ahead of a full lane")
                if consumed == capacity + 1:
                    bound_reached.set()
                yield hack(f"a{i}")

        uc = CheckDomainsUseCase(
            to, CollectingWriter(), parallel=4, lane_key=by_host, lane_capacity=capacity
        )
        run = Background(uc, source())
        try:
            assert bound_reached.wait(WAIT_TIMEOUT)
        finally:
            gate.set()
        assert run.join().checked == 10_000


class TestLaneScheduler:
    def _noop(self, domain: DomainHack) -> DomainCheckResult:
        return DomainCheckResult(domain=domain, availability=Availability.TAKEN)

    def test_validation(self) -> None:
        with pytest.raises(ValueError, match="workers"):
            LaneScheduler(self._noop, workers=0, lane_capacity=1)
        with pytest.raises(ValueError, match="lane_capacity"):
            LaneScheduler(self._noop, workers=1, lane_capacity=0)

    def test_no_submissions_after_close(self) -> None:
        scheduler = LaneScheduler(self._noop, workers=1, lane_capacity=1)
        scheduler.close()
        with pytest.raises(RuntimeError):
            scheduler.try_submit("lane", 0, hack())
        assert scheduler.next_outcome(timeout=0) is None
        assert scheduler.worker_count == 0

    def test_workers_capped_by_lanes_and_parallel(self) -> None:
        scheduler = LaneScheduler(self._noop, workers=2, lane_capacity=5)
        for i, lane in enumerate(["x", "x", "y", "z"]):
            assert scheduler.try_submit(lane, i, hack(f"a{i}"))
        outcomes = [scheduler.next_outcome() for _ in range(4)]
        scheduler.close()
        assert sorted(o.index for o in outcomes if o is not None) == [0, 1, 2, 3]
        assert scheduler.worker_count == 2

    def test_blocking_wait_polls_until_an_outcome_arrives(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gate = threading.Event()

        class CountingQueue(queue.SimpleQueue[LaneOutcome]):
            """Opens the gate on the third poll, so the first two time out."""

            polls = 0

            def get(self, block: bool = True, timeout: float | None = None) -> LaneOutcome:
                CountingQueue.polls += 1
                if CountingQueue.polls == 3:
                    gate.set()
                return super().get(block, timeout)

        def check(domain: DomainHack) -> DomainCheckResult:
            assert gate.wait(WAIT_TIMEOUT)
            return self._noop(domain)

        monkeypatch.setattr(lanes, "_POLL_SECONDS", 0.001)
        scheduler = LaneScheduler(check, workers=1, lane_capacity=1)
        monkeypatch.setattr(scheduler, "_outcomes", CountingQueue())
        scheduler.try_submit("x", 0, hack())
        outcome = scheduler.next_outcome()
        assert outcome is not None and outcome.index == 0
        assert CountingQueue.polls >= 3
        scheduler.stop(grace=WAIT_TIMEOUT)


class TestLaneFor:
    def _rdap(self, base: str) -> RdapRegistrarClient:
        http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
        return RdapRegistrarClient(base, client=http)

    def test_tlds_sharing_an_rdap_host_share_a_lane(self) -> None:
        identity = "https://rdap.identitydigital.services/rdap/"
        io, sh = self._rdap(identity), self._rdap(identity)
        to = self._rdap("https://rdap.tonicregistry.to/rdap/")
        assert lane_for(io, TLD("io")) == lane_for(sh, TLD("sh")) == "rdap.identitydigital.services"
        assert lane_for(to, TLD("to")) == "rdap.tonicregistry.to"

    def test_whois_lane_is_the_server_host(self) -> None:
        client = WhoisRegistrarClient(servers=WHOIS_SERVERS)
        tld = next(iter(WHOIS_SERVERS))
        assert lane_for(client, TLD(tld)) == WHOIS_SERVERS[tld].host
        # A suffix the client has no server for: a lane per client instance.
        assert lane_for(client, TLD("zz")) == ("client", id(client))

    def test_unknown_clients_get_a_lane_per_instance(self) -> None:
        a, b = ScriptedRegistrar(), ScriptedRegistrar()
        assert lane_for(a, TLD("to")) == lane_for(a, TLD("io"))
        assert lane_for(a, TLD("to")) != lane_for(b, TLD("to"))
        assert lane_for(None, TLD("to")) == lane_for(None, TLD("io"))

    def test_cli_lane_key_resolves_each_tld_once(self) -> None:
        catalog = FakeCatalog(ScriptedRegistrar())
        router = RegistrarRouter(lambda tld: catalog(tld, delay=0, breaker=None, contact=None))
        key: LaneKey = app._lane_key(argparse.Namespace(), router)
        assert key(hack("a", "to")) == key(hack("b", "to")) == key(hack("a", "io"))
        assert [c.tld.suffix for c in catalog.calls] == ["to", "io"]


class TestAdaptiveSlowDownPerHost:
    def test_slow_host_does_not_slow_the_other(self) -> None:
        from domainhack.adapters._throttle import HostThrottle

        clock = FakeClock()
        throttle = HostThrottle(clock=clock.time, sleep=lambda s: None, random=lambda: 0.5)

        def client(base: str, status: int) -> RdapRegistrarClient:
            http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status)))
            return RdapRegistrarClient(
                base, delay=0.0, client=http, throttle=throttle, max_retries=0
            )

        slow = client("https://slow.example/", 429)
        fast = client("https://fast.example/", 404)
        router = router_for({"to": slow, "io": fast})

        def lane(d: DomainHack) -> Hashable:
            return lane_for(router.client_for(d.tld), d.tld)

        writer = CollectingWriter()
        items = [hack(f"a{i}", s) for i in range(3) for s in ("to", "io")]
        summary = CheckDomainsUseCase(router, writer, parallel=2, lane_key=lane).execute(items)
        assert summary == CheckSummary(available=3, errors=3)
        assert throttle.interval("slow.example") >= 4.0  # slowed down three times
        assert throttle.interval("fast.example") == 0.0


# ── CLI ──────────────────────────────────────────────────────────────────────


def _check(*extra: str) -> list[str]:
    return [
        "--tld",
        "to,io",
        "check",
        "--range-max",
        "1",
        "--range-end",
        "c",
        "--no-progress",
        "--no-cache",
        "--show-taken",
        *extra,
    ]


class TestCli:
    def test_parallel_option(self) -> None:
        args = build_parser().parse_args(["check", "--file", "w.txt"])
        assert args.parallel == DEFAULT_PARALLEL == 4
        assert args.keep_order is False
        assert main(["check", "--file", "w.txt", "--parallel", "0"]) == EXIT_USAGE

    def test_parallel_1_output_matches_input_order(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        to, io = ScriptedRegistrar(raw_title="to"), ScriptedRegistrar(raw_title="io")
        catalog = FakeCatalog(by_tld={"to": to, "io": io})
        assert main(_check("--parallel", "1"), catalog=catalog) == EXIT_OK
        out = capsys.readouterr().out.split()
        assert [w for w in out if "." in w] == ["a.to", "a.io", "b.to", "b.io", "c.to", "c.io"]

    def test_default_parallel_checks_every_domain(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        to, io = ScriptedRegistrar(raw_title="to"), ScriptedRegistrar(raw_title="io")
        catalog = FakeCatalog(by_tld={"to": to, "io": io})
        assert main(_check(), catalog=catalog) == EXIT_OK
        captured = capsys.readouterr()
        out = [w for w in captured.out.split() if "." in w]
        assert sorted(out) == ["a.io", "a.to", "b.io", "b.to", "c.io", "c.to"]
        assert to.calls == ["a.to", "b.to", "c.to"]
        assert io.calls == ["a.io", "b.io", "c.io"]
        assert "Done. Checked 6 domains: 0 available, 6 taken, 0 errors." in captured.err
        assert "2 hosts in parallel, one request at a time each" in captured.err

    def test_keep_order_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        probe = ConcurrencyProbe()
        io = GatedRegistrar("io", probe)
        # a.to waits until the whole .io lane is done, so completion order differs.
        to = GatedRegistrar("to", probe, gates={"a": io.started("c.io")})
        catalog = FakeCatalog(by_tld={"to": to, "io": io})
        assert main(_check("--keep-order"), catalog=catalog) == EXIT_OK
        out = [w for w in capsys.readouterr().out.split() if "." in w]
        assert out == ["a.to", "a.io", "b.to", "b.io", "c.to", "c.io"]

    def test_limit_per_tld_holds(self, tmp_path: Path) -> None:
        to, io = ScriptedRegistrar(raw_title="to"), ScriptedRegistrar(raw_title="io")
        catalog = FakeCatalog(by_tld={"to": to, "io": io})
        argv = ["--tld", "to,io", "check", "--range-max", "2", "--limit", "2", "--no-progress"]
        assert main([*argv, "--no-cache"], catalog=catalog) == EXIT_OK
        assert to.calls == ["a.to", "b.to"]
        assert io.calls == ["a.io", "b.io"]

    def test_cache_is_used_on_the_main_thread(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "c.sqlite3"
        first = ScriptedRegistrar({"a": Availability.AVAILABLE})
        argv = [
            "--tld",
            "to,io",
            "check",
            "--range-max",
            "1",
            "--range-end",
            "c",
            "--no-progress",
            "--cache-path",
            str(cache_path),
        ]
        assert main(argv, catalog=FakeCatalog(first)) == EXIT_OK
        assert len(first.calls) == 6
        second = ScriptedRegistrar()
        assert main(argv, catalog=FakeCatalog(second)) == EXIT_OK
        assert second.calls == []

    def test_single_host_has_no_parallel_note(self, capsys: pytest.CaptureFixture[str]) -> None:
        argv = ["check", "--range-max", "1", "--range-end", "a", "--no-progress", "--no-cache"]
        assert main(argv, catalog=FakeCatalog(ScriptedRegistrar())) == EXIT_OK
        err = capsys.readouterr().err
        assert "Estimated 1 queries to 1 host (.to): at least 1 s at the current pacing." in err


class _AdvanceSignal(ProgressReporter):
    def __init__(self, after: int, event: threading.Event) -> None:
        self._after = after
        self._event = event
        self._count = 0

    def start(self, total: int | None) -> None:
        pass

    def advance(self, result: DomainCheckResult) -> None:
        self._count += 1
        if self._count == self._after:
            self._event.set()

    def close(self) -> None:
        pass


class TestCliCtrlC:
    def test_ctrl_c_while_a_host_hangs(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # A shell runs background jobs with SIGINT ignored; make Ctrl-C raise here anyway.
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
        monkeypatch.setattr(check_domains, "SHUTDOWN_GRACE_SECONDS", 0.01)
        monkeypatch.setattr(lanes, "_POLL_SECONDS", 0.01)
        written = threading.Event()
        monkeypatch.setattr(app, "_build_progress", lambda args: _AdvanceSignal(3, written))
        probe = ConcurrencyProbe()
        hang = threading.Event()  # never set during the run: .io never answers
        to = GatedRegistrar("to", probe, default=Availability.AVAILABLE)
        io = GatedRegistrar("io", probe, gates={"a.io": hang})
        catalog = FakeCatalog(by_tld={"to": to, "io": io})

        def press_ctrl_c() -> None:
            # Once the three .to results are written, the main thread can
            # only be waiting for .io: that is when the user gives up.
            if written.wait(WAIT_TIMEOUT):
                _thread.interrupt_main()

        out = tmp_path / "partial.csv"
        presser = threading.Thread(target=press_ctrl_c, daemon=True)
        presser.start()
        try:
            code = main(_check("--output", str(out)), catalog=catalog)
        finally:
            hang.set()
            presser.join(WAIT_TIMEOUT)
            signal.signal(signal.SIGINT, previous)
        assert code == EXIT_INTERRUPTED
        rows = out.read_text(encoding="utf-8").splitlines()
        assert [r.split(",")[0] for r in rows[1:]] == ["a.to", "b.to", "c.to"]
        err = capsys.readouterr().err
        assert "Interrupted after 3 checks (3 available, 0 errors)." in err
        assert "Traceback" not in err
        assert io.calls == [] or io.calls == ["a.io"]
        assert to.closed and io.closed


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_real_sigint_exits_130_without_hanging(tmp_path: Path) -> None:
    """A real Ctrl-C while a host hangs: exit 130, output flushed, no stuck process."""
    out = tmp_path / "partial.csv"
    child = subprocess.Popen(
        [sys.executable, "-m", "tests.parallel_sigint_child", str(out)],
        cwd=TESTS_DIR.parent,
        env=guarded_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stderr is not None
        for line in child.stderr:
            if line.strip() == "waiting":
                break
        else:  # pragma: no cover - the child died early
            pytest.fail(f"child exited early with {child.wait()}")
        child.send_signal(signal.SIGINT)
        stdout, stderr = child.communicate(timeout=WAIT_TIMEOUT)
    finally:
        if child.poll() is None:  # pragma: no cover - only when the test fails
            child.kill()
            child.wait()
    assert child.returncode == EXIT_INTERRUPTED, stderr
    assert "Interrupted after 3 checks (3 available, 0 errors)." in stderr
    assert "Traceback" not in stderr
    rows = out.read_text(encoding="utf-8").splitlines()
    assert [r.split(",")[0] for r in rows[1:]] == ["a.to", "b.to", "c.to"]
    assert stdout.count("AVAILABLE") == 3
