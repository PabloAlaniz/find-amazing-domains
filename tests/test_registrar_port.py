from tests.conftest import FakeRegistrarClient


class TestRegistrarClientPort:
    def test_default_context_manager(self) -> None:
        client = FakeRegistrarClient({})
        with client as c:
            assert c is client

    def test_default_close_is_noop(self) -> None:
        client = FakeRegistrarClient({})
        client.close()  # should not raise
