import pytest

from domainhack.adapters.registrar_router import RegistrarRouter
from domainhack.domain.entities import TLD, Availability, DomainCheckResult, DomainHack
from domainhack.ports.registrar import RegistrarClient

TO = TLD("to")
IO = TLD("io")
IN = TLD("in")


class FakeClient(RegistrarClient):
    def __init__(self, name: str, fail_on_close: bool = False) -> None:
        self.name = name
        self.calls: list[str] = []
        self.closed = False
        self._fail_on_close = fail_on_close

    def check_availability(self, domain: DomainHack) -> DomainCheckResult:
        self.calls.append(domain.fqdn)
        return DomainCheckResult(
            domain=domain, availability=Availability.AVAILABLE, raw_title=self.name
        )

    def close(self) -> None:
        self.closed = True
        if self._fail_on_close:
            raise RuntimeError(f"{self.name} close failed")


class RecordingFactory:
    def __init__(self, supported: dict[str, FakeClient]) -> None:
        self._supported = supported
        self.calls: list[str] = []

    def __call__(self, tld: TLD) -> RegistrarClient | None:
        self.calls.append(tld.suffix)
        return self._supported.get(tld.suffix)


class TestRegistrarRouter:
    def test_routes_by_tld(self) -> None:
        to_client, io_client = FakeClient("to"), FakeClient("io")
        router = RegistrarRouter(RecordingFactory({"to": to_client, "io": io_client}))

        r1 = router.check_availability(DomainHack.from_sld("pla", TO))
        r2 = router.check_availability(DomainHack.from_sld("rad", IO))

        assert r1.raw_title == "to"
        assert r2.raw_title == "io"
        assert to_client.calls == ["pla.to"]
        assert io_client.calls == ["rad.io"]

    def test_lazy_and_memoized(self) -> None:
        factory = RecordingFactory({"to": FakeClient("to")})
        router = RegistrarRouter(factory)
        assert factory.calls == []

        for sld in ("a", "b", "c"):
            router.check_availability(DomainHack.from_sld(sld, TO))
        router.check_availability(DomainHack.from_sld("a", IN))
        router.check_availability(DomainHack.from_sld("b", IN))

        assert factory.calls == ["to", "in"]

    def test_unsupported_tld_returns_error_without_client(self) -> None:
        factory = RecordingFactory({})
        router = RegistrarRouter(factory)
        domain = DomainHack.from_sld("berl", IN)

        result = router.check_availability(domain)

        assert result.availability is Availability.ERROR
        assert result.error_message == "No registrar supports .in"
        assert result.domain == domain

    def test_supports(self) -> None:
        factory = RecordingFactory({"to": FakeClient("to")})
        router = RegistrarRouter(factory)
        assert router.supports(TO) is True
        assert router.supports(IN) is False
        router.check_availability(DomainHack.from_sld("a", TO))
        assert factory.calls == ["to", "in"]

    def test_close_closes_all_created_clients(self) -> None:
        to_client, io_client = FakeClient("to"), FakeClient("io")
        router = RegistrarRouter(RecordingFactory({"to": to_client, "io": io_client}))
        router.check_availability(DomainHack.from_sld("a", TO))
        router.check_availability(DomainHack.from_sld("a", IN))  # unsupported: nothing to close

        with router:
            pass

        assert to_client.closed is True
        assert io_client.closed is False  # never created by the router

    def test_close_continues_after_failure_and_reraises(self) -> None:
        bad, good = FakeClient("to", fail_on_close=True), FakeClient("io")
        router = RegistrarRouter(RecordingFactory({"to": bad, "io": good}))
        router.supports(TO)
        router.supports(IO)

        with pytest.raises(RuntimeError, match="to close failed"):
            router.close()

        assert bad.closed is True
        assert good.closed is True
