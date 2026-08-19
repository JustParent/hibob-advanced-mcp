"""Shared test fixtures.

Every test runs against a mocked HiBob API; nothing here touches the network.
"""

from __future__ import annotations

import pytest
import respx
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from hibob_advanced_mcp import client as client_module
from hibob_advanced_mcp.client import HiBobClient
from hibob_advanced_mcp.config import (
    ENV_API_HOST,
    ENV_READ_ONLY,
    ENV_SERVICE_USER_ID,
    ENV_SERVICE_USER_TOKEN,
    load_settings,
)
from hibob_advanced_mcp.workforce_planning import register_workforce_planning_tools

API_BASE = "https://api.hibob.com/v1"
TEST_USER_ID = "svc-user-1"
TEST_TOKEN = "svc-token-1"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """Give every test a known environment and a fresh client singleton."""
    monkeypatch.setenv(ENV_SERVICE_USER_ID, TEST_USER_ID)
    monkeypatch.setenv(ENV_SERVICE_USER_TOKEN, TEST_TOKEN)
    monkeypatch.delenv(ENV_API_HOST, raising=False)
    monkeypatch.delenv(ENV_READ_ONLY, raising=False)
    client_module.reset_client()
    yield
    client_module.reset_client()


@pytest.fixture
def mock_api():
    """Route all HiBob calls to a respx mock."""
    with respx.mock(base_url=API_BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
def recorded_sleeps() -> list[float]:
    return []


@pytest.fixture
def client(recorded_sleeps: list[float]) -> HiBobClient:
    """A client whose retry backoff is recorded instead of actually slept."""

    async def fake_sleep(delay: float) -> None:
        recorded_sleeps.append(delay)

    return HiBobClient(load_settings(), sleep=fake_sleep)


@pytest.fixture
def server_factory(client: HiBobClient):
    """Build a server whose tools talk to the test client."""

    def build(read_only: bool = False) -> FastMCP:
        mcp = FastMCP("hibob_advanced_mcp_test")
        register_workforce_planning_tools(
            mcp, read_only=read_only, client_factory=lambda: client
        )
        return mcp

    return build


@pytest.fixture
def mcp_server(server_factory) -> FastMCP:
    return server_factory()


async def call_tool(mcp: FastMCP, name: str, arguments: dict | None = None) -> str:
    """Invoke a tool and return its text result.

    Newer SDK versions return ``(content, structured)``; older ones return the
    content sequence alone.
    """
    result = await mcp.call_tool(name, arguments or {})
    content = result[0] if isinstance(result, tuple) else result
    return "".join(block.text for block in content if isinstance(block, TextContent))
