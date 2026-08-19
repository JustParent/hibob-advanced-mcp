"""HTTP client behaviour: auth, base URL, retries and headers."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from hibob_advanced_mcp.client import HiBobClient
from hibob_advanced_mcp.config import (
    ENV_API_HOST,
    ENV_SERVICE_USER_TOKEN,
    load_settings,
)
from hibob_advanced_mcp.errors import HiBobApiError, HiBobConfigError

from conftest import TEST_TOKEN, TEST_USER_ID


async def test_uses_basic_auth_with_service_user_credentials(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get("/metadata/objects/position").mock(
        return_value=httpx.Response(200, json={"fields": []})
    )

    await client.get("/metadata/objects/position")

    header = route.calls.last.request.headers["Authorization"]
    expected = base64.b64encode(f"{TEST_USER_ID}:{TEST_TOKEN}".encode()).decode()
    assert header == f"Basic {expected}"


async def test_sends_no_default_content_type_on_get(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    """httpx must choose Content-Type per request, so no default is set."""
    route = mock_api.get("/metadata/objects/position").mock(
        return_value=httpx.Response(200, json={})
    )

    await client.get("/metadata/objects/position")

    assert "content-type" not in route.calls.last.request.headers


async def test_targets_sandbox_host_when_configured(
    monkeypatch: pytest.MonkeyPatch, recorded_sleeps: list[float]
) -> None:
    monkeypatch.setenv(ENV_API_HOST, "api.sandbox.hibob.com")

    async def fake_sleep(delay: float) -> None:
        recorded_sleeps.append(delay)

    client = HiBobClient(load_settings(), sleep=fake_sleep)

    with respx.mock(base_url="https://api.sandbox.hibob.com/v1") as router:
        route = router.get("/metadata/objects/position").mock(
            return_value=httpx.Response(200, json={})
        )
        await client.get("/metadata/objects/position")

    assert route.called


async def test_missing_credentials_raise_actionable_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_SERVICE_USER_TOKEN, raising=False)
    client = HiBobClient(load_settings())

    with pytest.raises(HiBobConfigError) as excinfo:
        await client.get("/metadata/objects/position")

    assert "HIBOB_SERVICE_USER_ID" in str(excinfo.value)
    assert "HIBOB_SERVICE_USER_TOKEN" in str(excinfo.value)


async def test_read_call_retries_on_rate_limit(
    client: HiBobClient, mock_api: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    route = mock_api.post("/objects/position/search").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}, json={}),
            httpx.Response(200, json=[]),
        ]
    )

    await client.search("/objects/position/search", {"fields": []})

    assert route.call_count == 2
    assert recorded_sleeps == [2.0]


async def test_read_call_retries_on_server_error_then_gives_up(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get("/metadata/objects/position").mock(
        return_value=httpx.Response(503, json={})
    )

    with pytest.raises(HiBobApiError) as excinfo:
        await client.get("/metadata/objects/position")

    assert route.call_count == 3  # original attempt plus two retries
    assert excinfo.value.status_code == 503


async def test_write_call_is_never_retried(
    client: HiBobClient, mock_api: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    """Creates are not idempotent, so a 429 must not be replayed."""
    route = mock_api.post("/workforce-planning/positions").mock(
        return_value=httpx.Response(429, json={})
    )

    with pytest.raises(HiBobApiError):
        await client.post("/workforce-planning/positions", {"items": []})

    assert route.call_count == 1
    assert recorded_sleeps == []


async def test_retry_delay_is_capped(
    client: HiBobClient, mock_api: respx.MockRouter, recorded_sleeps: list[float]
) -> None:
    mock_api.get("/metadata/objects/position").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "3600"}, json={})
    )

    with pytest.raises(HiBobApiError):
        await client.get("/metadata/objects/position")

    assert recorded_sleeps == [10.0, 10.0]


async def test_empty_response_body_returns_none(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    mock_api.patch("/workforce-planning/positions/1/cancel").mock(
        return_value=httpx.Response(204)
    )

    assert await client.patch("/workforce-planning/positions/1/cancel") is None
