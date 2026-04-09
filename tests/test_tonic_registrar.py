from unittest.mock import MagicMock, patch

import httpx

from domainhack.adapters.tonic_registrar import TonicRegistrarClient
from domainhack.domain.entities import TLD, Availability, DomainHack


def _make_transport(body: dict, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def _make_client(transport: httpx.MockTransport) -> TonicRegistrarClient:
    client = TonicRegistrarClient(delay=0.0)
    client._client = httpx.Client(transport=transport)
    return client


class TestTonicRegistrarClient:
    def test_available_domain(self) -> None:
        body = {
            "type": "success",
            "status": 200,
            "data": '[{"error":1,"domain":2,"transferMode":3},null,"xyzqwkj.to",false]',
        }
        client = _make_client(_make_transport(body))
        hack = DomainHack.from_word("plato", TLD("to"))
        assert hack is not None

        result = client.check_availability(hack)
        assert result.availability == Availability.AVAILABLE
        assert result.raw_title == "success"

    def test_taken_domain(self) -> None:
        body = {
            "type": "failure",
            "status": 409,
            "data": '[{"error":1},"DOMAIN_TAKEN"]',
        }
        client = _make_client(_make_transport(body))
        hack = DomainHack.from_word("plato", TLD("to"))
        assert hack is not None

        result = client.check_availability(hack)
        assert result.availability == Availability.TAKEN

    def test_unknown_response(self) -> None:
        body = {"type": "unknown", "status": 500}
        client = _make_client(_make_transport(body))
        hack = DomainHack.from_word("plato", TLD("to"))
        assert hack is not None

        result = client.check_availability(hack)
        assert result.availability == Availability.ERROR

    def test_network_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        transport = httpx.MockTransport(handler)
        client = _make_client(transport)
        hack = DomainHack.from_word("plato", TLD("to"))
        assert hack is not None

        result = client.check_availability(hack)
        assert result.availability == Availability.ERROR
        assert "connection refused" in result.error_message

    def test_context_manager_closes_client(self) -> None:
        client = TonicRegistrarClient(delay=0.0)
        mock_http = MagicMock()
        client._client = mock_http

        with client:
            pass

        mock_http.close.assert_called_once()

    def test_delay_between_requests(self) -> None:
        body = {"type": "success", "status": 200}
        client = _make_client(_make_transport(body))
        client._delay = 0.5

        hack = DomainHack.from_word("plato", TLD("to"))
        assert hack is not None

        with patch("domainhack.adapters.tonic_registrar.time.sleep") as mock_sleep:
            client.check_availability(hack)
            mock_sleep.assert_not_called()

            client.check_availability(hack)
            mock_sleep.assert_called_once_with(0.5)

    def test_no_delay_first_request(self) -> None:
        body = {"type": "success", "status": 200}
        client = _make_client(_make_transport(body))
        client._delay = 1.0

        hack = DomainHack.from_word("plato", TLD("to"))
        assert hack is not None

        with patch("domainhack.adapters.tonic_registrar.time.sleep") as mock_sleep:
            client.check_availability(hack)
            mock_sleep.assert_not_called()

    def test_sends_correct_payload(self) -> None:
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json={"type": "success", "status": 200})

        transport = httpx.MockTransport(handler)
        client = _make_client(transport)
        hack = DomainHack.from_word("plato", TLD("to"))
        assert hack is not None

        client.check_availability(hack)
        assert len(captured_requests) == 1
        assert b"domain=pla" in captured_requests[0].content
