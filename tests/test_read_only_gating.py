"""Read-only mode must withhold write tools entirely, not just refuse them."""

from __future__ import annotations

import pytest
from mcp.server.fastmcp import FastMCP

READ_TOOLS = {
    "hibob_list_workforce_fields",
    "hibob_get_company_named_lists",
    "hibob_search_positions",
    "hibob_search_position_openings",
    "hibob_search_position_budgets",
}

WRITE_TOOLS = {
    "hibob_create_position",
    "hibob_update_position",
    "hibob_cancel_position",
    "hibob_create_position_opening",
    "hibob_update_position_opening",
    "hibob_delete_position_opening",
    "hibob_create_position_budget",
    "hibob_update_position_budget",
}

DESTRUCTIVE_TOOLS = {"hibob_cancel_position", "hibob_delete_position_opening"}


async def _tool_names(mcp: FastMCP) -> set[str]:
    return {tool.name for tool in await mcp.list_tools()}


async def test_full_server_registers_every_tool(server_factory) -> None:
    assert await _tool_names(server_factory()) == READ_TOOLS | WRITE_TOOLS


async def test_read_only_server_registers_only_read_tools(server_factory) -> None:
    assert await _tool_names(server_factory(read_only=True)) == READ_TOOLS


async def test_read_only_server_cannot_be_asked_to_write(server_factory) -> None:
    mcp = server_factory(read_only=True)
    with pytest.raises(Exception):
        await mcp.call_tool("hibob_create_position", {})


async def test_read_tools_are_annotated_read_only(server_factory) -> None:
    for tool in await server_factory().list_tools():
        if tool.name in READ_TOOLS:
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.destructiveHint is False


async def test_only_cancel_and_delete_are_annotated_destructive(
    server_factory,
) -> None:
    for tool in await server_factory().list_tools():
        if tool.name in WRITE_TOOLS:
            assert tool.annotations.readOnlyHint is False
            assert tool.annotations.destructiveHint is (
                tool.name in DESTRUCTIVE_TOOLS
            )


async def test_every_tool_has_a_title_and_description(server_factory) -> None:
    for tool in await server_factory().list_tools():
        assert tool.annotations.title
        assert tool.description and len(tool.description) > 40
