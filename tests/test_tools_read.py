"""Read tool tests: routing, request bodies and result shaping."""

from __future__ import annotations

import json

import httpx
import respx
from conftest import call_tool
from mcp.server.fastmcp import FastMCP


async def test_metadata_routes_per_object_type(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """The three metadata endpoints do not share a path convention."""
    position = mock_api.get("/metadata/objects/position").mock(
        return_value=httpx.Response(200, json={"fields": ["a"]})
    )
    opening = mock_api.get("/positions/position-openings/metadata").mock(
        return_value=httpx.Response(200, json={"fields": ["b"]})
    )
    budget = mock_api.get("/positions/position-budget/metadata").mock(
        return_value=httpx.Response(200, json={"fields": ["c"]})
    )

    await call_tool(mcp_server, "hibob_list_workforce_fields", {"object_type": "position"})
    await call_tool(
        mcp_server, "hibob_list_workforce_fields", {"object_type": "positionOpening"}
    )
    await call_tool(
        mcp_server, "hibob_list_workforce_fields", {"object_type": "positionBudget"}
    )

    assert position.called and opening.called and budget.called


async def test_named_lists_fetches_all_or_one(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    all_lists = mock_api.get("/company/named-lists").mock(
        return_value=httpx.Response(200, json={"department": {}})
    )
    one_list = mock_api.get("/company/named-lists/department").mock(
        return_value=httpx.Response(200, json={"values": []})
    )

    await call_tool(mcp_server, "hibob_get_company_named_lists")
    await call_tool(
        mcp_server, "hibob_get_company_named_lists", {"list_name": "department"}
    )

    assert all_lists.called and one_list.called


async def test_position_search_builds_expected_body(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("/objects/position/search").mock(
        return_value=httpx.Response(200, json=[])
    )

    await call_tool(
        mcp_server,
        "hibob_search_positions",
        {
            "fields": ["/position/id", "/position/name"],
            "filters": [
                {
                    "field_id": "/position/status",
                    "operator": "equals",
                    "values": ["vacant"],
                }
            ],
            "include_human_readable": True,
        },
    )

    assert json.loads(route.calls.last.request.content) == {
        "fields": ["/position/id", "/position/name"],
        "filters": [
            {
                "fieldId": "/position/status",
                "operator": "equals",
                "values": ["vacant"],
            }
        ],
        "includeHumanReadable": True,
    }


async def test_position_search_flattens_entries(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post("/objects/position/search").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "/position/name": {"value": "P-1", "humanReadable": "P-1"},
                    "/position/status": {"value": "vacant", "humanReadable": "Vacant"},
                }
            ],
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server, "hibob_search_positions", {"fields": ["/position/name"]}
        )
    )

    assert result["count"] == 1
    assert result["entries"][0]["values"]["/position/status"] == "vacant"
    assert result["entries"][0]["display"]["/position/status"] == "Vacant"


async def test_position_search_reads_wrapped_entries(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """HiBob may wrap results in an object rather than returning a bare list."""
    mock_api.post("/objects/position/search").mock(
        return_value=httpx.Response(
            200, json={"positionEntries": [{"/position/id": {"value": 5}}]}
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server, "hibob_search_positions", {"fields": ["/position/id"]}
        )
    )

    assert result["entries"] == [{"values": {"/position/id": 5}}]


async def test_openings_search_sends_pagination_and_returns_cursor(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("/positions/position-openings/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "positionOpeningEntries": [{"/positionOpening/id": {"value": 9}}],
                "response_metadata": {"next_cursor": "abc123"},
            },
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_search_position_openings",
            {"fields": ["/positionOpening/id"], "limit": 50, "cursor": "prev"},
        )
    )

    assert json.loads(route.calls.last.request.content)["pagination"] == {
        "limit": 50,
        "cursor": "prev",
    }
    assert result["next_cursor"] == "abc123"
    assert result["has_more"] is True


async def test_openings_search_without_cursor_reports_no_more(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("/positions/position-openings/search").mock(
        return_value=httpx.Response(
            200, json={"positionOpeningEntries": [], "response_metadata": {}}
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_search_position_openings",
            {"fields": ["/positionOpening/id"]},
        )
    )

    assert "cursor" not in json.loads(route.calls.last.request.content)["pagination"]
    assert result["has_more"] is False
    assert "next_cursor" not in result


async def test_paged_search_tolerates_bare_list_response(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post("/positions/position-openings/search").mock(
        return_value=httpx.Response(200, json=[{"/positionOpening/id": {"value": 3}}])
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_search_position_openings",
            {"fields": ["/positionOpening/id"]},
        )
    )

    assert result["count"] == 1
    assert result["has_more"] is False


async def test_budget_search_uses_budget_endpoint(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("/positions/position-budget/search").mock(
        return_value=httpx.Response(200, json={"positionBudgetEntries": []})
    )

    await call_tool(
        mcp_server,
        "hibob_search_position_budgets",
        {"fields": ["/positionBudget/currency"]},
    )

    assert route.called


async def test_search_without_fields_fails_before_any_request(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("/objects/position/search")

    result = await call_tool(mcp_server, "hibob_search_positions", {"fields": []})

    assert result.startswith("Error:")
    assert "At least one field ID is required" in result
    assert not route.called


async def test_permission_error_is_returned_as_guidance_not_traceback(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post("/objects/position/search").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"})
    )

    result = await call_tool(
        mcp_server, "hibob_search_positions", {"fields": ["/position/id"]}
    )

    assert result.startswith("Error:")
    assert "Manage positions" in result
