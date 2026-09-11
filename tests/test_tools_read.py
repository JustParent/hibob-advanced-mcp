"""Read tool tests: routing, request bodies and result shaping."""

from __future__ import annotations

import json

import httpx
import respx
from mcp.server.fastmcp import FastMCP

from conftest import call_tool


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

    await call_tool(
        mcp_server, "hibob_list_workforce_fields", {"object_type": "position"}
    )
    await call_tool(
        mcp_server, "hibob_list_workforce_fields", {"object_type": "positionOpening"}
    )
    await call_tool(
        mcp_server, "hibob_list_workforce_fields", {"object_type": "positionBudget"}
    )

    assert position.called and opening.called and budget.called


async def test_named_lists_without_a_name_returns_only_names_and_sizes(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """The combined endpoint can run to tens of megabytes, so the tool only
    summarises it; items come from a single-list call."""
    all_lists = mock_api.get("/company/named-lists").mock(
        return_value=httpx.Response(
            200,
            json={
                "department": {
                    "name": "department",
                    "values": [{"id": 10, "name": "Engineering"}],
                    "items": [{"id": 10, "name": "Engineering"}],
                },
                "site": {
                    "name": "site",
                    "items": [
                        {
                            "id": 20,
                            "name": "UK",
                            "children": [{"id": 21, "name": "London"}],
                        }
                    ],
                },
            },
        )
    )

    text = await call_tool(mcp_server, "hibob_get_company_named_lists")
    result = json.loads(text)

    assert all_lists.call_count == 1
    assert result["count"] == 2
    assert result["lists"] == [
        {"name": "department", "items": 1},
        {"name": "site", "items": 2},
    ]
    assert "list_name" in result["note"]
    assert "Engineering" not in text and "London" not in text


async def test_named_lists_by_name_returns_shaped_items(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """HiBob echoes every item twice ("values" and "items"); only the fields a
    caller needs to pick and submit an item are returned."""
    mock_api.get("/company/named-lists/department").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "department",
                "values": [{"id": "10", "value": "Eng", "name": "Eng"}],
                "items": [
                    {
                        "id": "10",
                        "value": "Eng",
                        "name": "Eng",
                        "archived": False,
                        "children": [],
                    },
                    {"id": "11", "value": "Old", "name": "Old", "archived": True},
                ],
            },
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server, "hibob_get_company_named_lists", {"list_name": "department"}
        )
    )

    assert result == {
        "name": "department",
        "count": 2,
        "items": [
            {"id": "10", "name": "Eng"},
            {"id": "11", "name": "Old", "archived": True},
        ],
    }


async def test_named_lists_can_include_archived_items(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get(
        "/company/named-lists/site", params__contains={"includeArchived": "true"}
    ).mock(return_value=httpx.Response(200, json={"name": "site", "items": []}))

    await call_tool(
        mcp_server,
        "hibob_get_company_named_lists",
        {"list_name": "site", "include_archived": True},
    )

    assert route.called


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
                "values": [{"/positionOpening/id": {"value": 9}}],
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
        return_value=httpx.Response(200, json={"values": [], "response_metadata": {}})
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
        return_value=httpx.Response(200, json={"values": []})
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


async def test_openings_search_reads_entries_from_values_key(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """HiBob's live API returns entries under "values", not the documented key."""
    mock_api.post("/positions/position-openings/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {
                        "/positionOpening/id": {"value": 13679213},
                        "/positionOpening/status": {
                            "value": "vacant",
                            "humanReadable": "Vacant",
                        },
                    }
                ],
                "response_metadata": {"next_cursor": None},
            },
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_search_position_openings",
            {"fields": ["/positionOpening/id", "/positionOpening/status"]},
        )
    )

    assert result["count"] == 1
    assert result["entries"][0]["values"]["/positionOpening/id"] == 13679213
    assert result["entries"][0]["display"]["/positionOpening/status"] == "Vacant"
    assert result["has_more"] is False


async def test_budget_search_reads_entries_from_values_key(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post("/positions/position-budget/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [{"/positionBudget/id": {"value": 45155535}}],
                "response_metadata": {"next_cursor": "page2"},
            },
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_search_position_budgets",
            {"fields": ["/positionBudget/id"]},
        )
    )

    assert result["count"] == 1
    assert result["entries"][0]["values"]["/positionBudget/id"] == 45155535
    assert result["next_cursor"] == "page2"


async def test_paged_search_accepts_documented_entries_key(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """HiBob's API reference documents "positionOpeningEntries" as a list of
    lists; the live API uses "values", but the documented shape still parses."""
    mock_api.post("/positions/position-openings/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "positionOpeningEntries": [[{"/positionOpening/id": {"value": 9}}]],
                "response_metadata": {"next_cursor": None},
            },
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_search_position_openings",
            {"fields": ["/positionOpening/id"]},
        )
    )

    assert result["count"] == 1
    assert result["entries"][0]["values"]["/positionOpening/id"] == 9


# ------------------------------------------------ searches without filters


MATCH_ALL = {"operator": "notEqual", "values": ["1"]}


async def test_position_search_without_filters_returns_every_position(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """HiBob refuses an empty filter list, so the server sends a clause every
    position satisfies."""
    route = mock_api.post("/objects/position/search").mock(
        return_value=httpx.Response(200, json=[])
    )

    await call_tool(mcp_server, "hibob_search_positions", {"fields": ["/position/id"]})

    assert json.loads(route.calls.last.request.content)["filters"] == [
        {"fieldId": "/position/id", **MATCH_ALL}
    ]


async def test_openings_search_without_filters_returns_every_opening(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("/positions/position-openings/search").mock(
        return_value=httpx.Response(200, json={"values": []})
    )

    await call_tool(
        mcp_server,
        "hibob_search_position_openings",
        {"fields": ["/positionOpening/id"]},
    )

    assert json.loads(route.calls.last.request.content)["filters"] == [
        {"fieldId": "/positionOpening/id", **MATCH_ALL}
    ]


async def test_budget_search_without_filters_returns_every_budget(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("/positions/position-budget/search").mock(
        return_value=httpx.Response(200, json={"values": []})
    )

    await call_tool(
        mcp_server, "hibob_search_position_budgets", {"fields": ["/positionBudget/id"]}
    )

    assert json.loads(route.calls.last.request.content)["filters"] == [
        {"fieldId": "/positionBudget/id", **MATCH_ALL}
    ]


# ------------------------------------------- free-text query on position search


def _position(
    position_id: int, title: str, department: str, site: str, holder: str | None
) -> dict:
    row = {
        "/position/id": {"value": position_id, "humanReadable": str(position_id)},
        "/position/name": {
            "value": f"P-{position_id}",
            "humanReadable": f"P-{position_id}",
        },
        "/position/position": {"value": title, "humanReadable": title},
        "/position/department": {"value": 1, "humanReadable": department},
        "/position/site": {"value": 2, "humanReadable": site},
        "/position/jobProfile": {
            "value": 3,
            "humanReadable": f"D {title.split(' /')[0]} (J-1)",
        },
        "/position/status": {"value": "filled", "humanReadable": "Filled"},
    }
    if holder:
        row["/position/filledBy"] = {"value": "9", "humanReadable": holder}
    return row


TITLES = [
    _position(
        7,
        "Manager, Customer Success / Customer Experience / Copenhagen - Office",
        "Customer Experience",
        "Copenhagen - Office",
        "Stina Grahn",
    ),
    _position(
        10,
        "Senior Manager, Customer Success / Customer Experience / Copenhagen - Office",
        "Customer Experience",
        "Copenhagen - Office",
        "Lars Holm",
    ),
    _position(
        8,
        "Manager, Customer Success / Customer Experience / Madrid - Office",
        "Customer Experience",
        "Madrid - Office",
        None,
    ),
    _position(
        9,
        "Account Executive / Commercial / Madrid - Office",
        "Commercial",
        "Madrid - Office",
        "Ana Ruiz",
    ),
]


async def test_position_search_query_matches_locally_on_title_site_and_holder(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """HiBob cannot filter on title, department, site, job profile or holder,
    so a query is matched here after one fetch of everything."""
    route = mock_api.post("/objects/position/search").mock(
        return_value=httpx.Response(200, json=TITLES)
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_search_positions",
            {
                "fields": ["/position/id", "/position/status"],
                "query": "Manager, Customer Success in Madrid",
            },
        )
    )

    body = json.loads(route.calls.last.request.content)
    assert body["filters"] == [
        {"fieldId": "/position/id", "operator": "notEqual", "values": ["1"]}
    ]
    assert {
        "/position/position",
        "/position/name",
        "/position/department",
        "/position/site",
        "/position/jobProfile",
        "/position/filledBy",
    } <= set(body["fields"])
    assert body["includeHumanReadable"] is True
    assert result["query"] == "Manager, Customer Success in Madrid"
    assert result["scanned"] == 4
    assert result["count"] == 1
    assert result["entries"][0]["values"]["/position/id"] == 8
    assert result["entries"][0]["display"]["/position/site"] == "Madrid - Office"


async def test_position_search_query_finds_the_exact_title_and_a_holder(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post("/objects/position/search").mock(
        return_value=httpx.Response(200, json=TITLES)
    )

    by_title = json.loads(
        await call_tool(
            mcp_server,
            "hibob_search_positions",
            {
                "fields": ["/position/id"],
                "query": "Manager, Customer Success / Customer Experience / Copenhagen - Office",
            },
        )
    )
    by_holder = json.loads(
        await call_tool(
            mcp_server,
            "hibob_search_positions",
            {"fields": ["/position/id"], "query": "ana ruiz"},
        )
    )

    assert [e["values"]["/position/id"] for e in by_title["entries"]] == [7]
    assert [e["values"]["/position/id"] for e in by_holder["entries"]] == [9]


async def test_position_search_query_with_no_match_is_empty_not_an_error(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post("/objects/position/search").mock(
        return_value=httpx.Response(200, json=TITLES)
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_search_positions",
            {"fields": ["/position/id"], "query": "plumber"},
        )
    )

    assert result["count"] == 0
    assert result["entries"] == []
    assert result["scanned"] == 4
    assert "note" not in result


async def test_position_search_query_needs_every_word_and_names_near_misses(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """Sharing one word with hundreds of positions is not a match; the
    nearest partial matches are named so the caller can refine the query."""
    mock_api.post("/objects/position/search").mock(
        return_value=httpx.Response(200, json=TITLES)
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_search_positions",
            {
                "fields": ["/position/id"],
                "query": "customer success manager madrid remote",
            },
        )
    )

    assert result["count"] == 0
    assert result["entries"] == []
    assert "No position matches every word" in result["note"]
    assert "Madrid - Office" in result["note"]
