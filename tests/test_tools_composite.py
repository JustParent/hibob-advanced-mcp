"""Tests for the tools that combine several HiBob calls into one result."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx
from mcp.server.fastmcp import FastMCP

from conftest import call_tool

OPENINGS_SEARCH = "/positions/position-openings/search"
POSITION_META = "/metadata/objects/position"
OPENING_META = "/positions/position-openings/metadata"
BUDGET_META = "/positions/position-budget/metadata"
NAMED_LISTS = "/company/named-lists"

MATCH_ALL = {"fieldId": "/positionOpening/id", "operator": "notEqual", "values": ["1"]}


def _opening(
    opening_id: int, position_id: Any, status: str = "vacant"
) -> dict[str, Any]:
    return {
        "/positionOpening/id": {"value": opening_id},
        "/positionOpening/positionId": {"value": position_id},
        "/positionOpening/status": {"value": status, "humanReadable": status.title()},
    }


def _page(entries: list[Any], next_cursor: str | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "positionOpeningEntries": entries,
            "response_metadata": {"next_cursor": next_cursor},
        },
    )


def _body(route: respx.Route, call_index: int = -1) -> dict[str, Any]:
    return json.loads(route.calls[call_index].request.content)


# ------------------------------------------------- hibob_get_openings_for_positions


async def test_openings_for_positions_sends_match_all_filter_and_join_fields(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(OPENINGS_SEARCH).mock(return_value=_page([]))

    await call_tool(
        mcp_server, "hibob_get_openings_for_positions", {"position_ids": ["7"]}
    )

    body = _body(route)
    assert body["filters"] == [MATCH_ALL]
    assert body["pagination"] == {"limit": 100}
    assert body["includeHumanReadable"] is True
    assert body["fields"][:2] == ["/positionOpening/id", "/positionOpening/positionId"]
    assert "/positionOpening/expectedStartDate" in body["fields"]
    assert len(body["fields"]) == len(set(body["fields"]))


async def test_openings_for_positions_pages_through_every_cursor_and_joins(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(OPENINGS_SEARCH).mock(
        side_effect=[
            _page([_opening(1, 7), _opening(2, 8)], "cursor-1"),
            _page([_opening(3, "7"), _opening(4, 9)], "cursor-2"),
            _page([_opening(5, 9.0)]),
        ]
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_get_openings_for_positions",
            {"position_ids": [7, "9", "7"]},
        )
    )

    assert route.call_count == 3
    assert "cursor" not in _body(route, 0)["pagination"]
    assert _body(route, 1)["pagination"] == {"limit": 100, "cursor": "cursor-1"}
    assert _body(route, 2)["pagination"] == {"limit": 100, "cursor": "cursor-2"}

    assert result["count"] == 4
    assert [e["values"]["/positionOpening/id"] for e in result["entries"]] == [
        1,
        3,
        4,
        5,
    ]
    assert result["counts_by_position"] == {"7": 2, "9": 2}
    assert result["openings_scanned"] == 5
    assert result["scan_complete"] is True
    assert "warning" not in result
    assert result["entries"][0]["display"]["/positionOpening/status"] == "Vacant"


async def test_openings_for_positions_reports_zero_for_positions_without_openings(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post(OPENINGS_SEARCH).mock(return_value=_page([_opening(1, 7)]))

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_get_openings_for_positions",
            {"position_ids": ["7", "42"]},
        )
    )

    assert result["counts_by_position"] == {"7": 1, "42": 0}
    assert result["count"] == 1


async def test_openings_for_positions_lets_hibob_apply_a_status_filter(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(OPENINGS_SEARCH).mock(return_value=_page([]))

    await call_tool(
        mcp_server,
        "hibob_get_openings_for_positions",
        {"position_ids": ["7"], "statuses": ["vacant", "starting"]},
    )

    assert _body(route)["filters"] == [
        {
            "fieldId": "/positionOpening/status",
            "operator": "equals",
            "values": ["vacant", "starting"],
        }
    ]


async def test_openings_for_positions_always_requests_join_fields_once(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(OPENINGS_SEARCH).mock(return_value=_page([]))

    await call_tool(
        mcp_server,
        "hibob_get_openings_for_positions",
        {
            "position_ids": ["7"],
            "fields": [
                "/positionOpening/positionId",
                "/positionOpening/expectedStartDate",
            ],
            "include_human_readable": False,
        },
    )

    body = _body(route)
    assert body["fields"] == [
        "/positionOpening/id",
        "/positionOpening/positionId",
        "/positionOpening/expectedStartDate",
    ]
    assert body["includeHumanReadable"] is False


async def test_openings_for_positions_stops_when_a_cursor_repeats(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(OPENINGS_SEARCH).mock(
        return_value=_page([_opening(1, 7)], "same-cursor")
    )

    result = json.loads(
        await call_tool(
            mcp_server, "hibob_get_openings_for_positions", {"position_ids": ["7"]}
        )
    )

    assert route.call_count == 2
    assert result["scan_complete"] is False
    assert "warning" in result
    assert result["count"] == 2  # both pages were kept


async def test_openings_for_positions_accepts_nested_entry_lists(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """HiBob's reference declares the entries as a list of lists."""
    mock_api.post(OPENINGS_SEARCH).mock(
        return_value=_page([[_opening(1, 7), _opening(2, 7)]])
    )

    result = json.loads(
        await call_tool(
            mcp_server, "hibob_get_openings_for_positions", {"position_ids": ["7"]}
        )
    )

    assert result["count"] == 2


async def test_openings_for_positions_rejects_blank_ids_before_any_request(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(OPENINGS_SEARCH)

    result = await call_tool(
        mcp_server, "hibob_get_openings_for_positions", {"position_ids": ["  "]}
    )

    assert result.startswith("Error:")
    assert not route.called


async def test_openings_for_positions_enforces_the_field_cap_before_any_request(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post(OPENINGS_SEARCH)

    result = await call_tool(
        mcp_server,
        "hibob_get_openings_for_positions",
        {
            "position_ids": ["7"],
            "fields": [f"/positionOpening/f{i}" for i in range(49)],
        },
    )

    assert result.startswith("Error:")
    assert "at most 50 fields" in result
    assert not route.called


async def test_openings_for_positions_surfaces_api_errors(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post(OPENINGS_SEARCH).mock(return_value=httpx.Response(403, json={}))

    result = await call_tool(
        mcp_server, "hibob_get_openings_for_positions", {"position_ids": ["7"]}
    )

    assert result.startswith("Error:")
    assert "Manage positions" in result


# --------------------------------------------------------- hibob_get_workforce_form


def _meta(field_id: str, list_id: str | None = None, field_type: str = "text") -> dict:
    descriptor: dict[str, Any] = {
        "id": field_id,
        "name": field_id.rsplit("/", 1)[-1],
        "fieldType": {"type": field_type},
    }
    if list_id:
        descriptor["fieldType"]["typeData"] = {"listId": list_id}
    return descriptor


POSITION_METADATA = [
    _meta("/position/id", field_type="number"),
    _meta("/position/department", "department", "list"),
    _meta("/position/site", "site", "list"),
    _meta("/position/jobProfile", "jobProfile", "list"),
    _meta("/position/effectiveDate", field_type="date"),
    _meta("/position/fte", field_type="number"),
]
OPENING_METADATA = [
    _meta("/positionOpening/expectedStartDate", field_type="date"),
    _meta("/positionOpening/recruitmentStatus", field_type="list"),
]
BUDGET_METADATA = [
    _meta("/positionBudget/currency", "currency", "list"),
    _meta("/positionBudget/salaryPayPeriod", field_type="list"),
]
ALL_LISTS = [
    {
        "name": "department",
        "items": [{"id": 10, "name": "Engineering", "value": "Engineering"}],
    },
    {"name": "site", "items": [{"id": 20, "name": "London", "value": "London"}]},
    {"name": "currency", "items": [{"id": "GBP", "name": "GBP", "value": "GBP"}]},
]


def _mock_metadata(mock_api: respx.MockRouter) -> None:
    mock_api.get(POSITION_META).mock(
        return_value=httpx.Response(200, json=POSITION_METADATA)
    )
    mock_api.get(OPENING_META).mock(
        return_value=httpx.Response(200, json=OPENING_METADATA)
    )
    mock_api.get(BUDGET_META).mock(
        return_value=httpx.Response(200, json=BUDGET_METADATA)
    )


def _sections(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {section["object_type"]: section for section in result["sections"]}


def _fields(section: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {field["id"]: field for field in section["fields"]}


async def test_position_form_joins_three_sections_with_named_lists(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    _mock_metadata(mock_api)
    all_lists = mock_api.get(NAMED_LISTS).mock(
        return_value=httpx.Response(200, json=ALL_LISTS)
    )
    job_profiles = mock_api.get(f"{NAMED_LISTS}/jobProfile").mock(
        return_value=httpx.Response(
            200, json={"name": "jobProfile", "items": [{"id": 30, "name": "Engineer"}]}
        )
    )

    result = json.loads(await call_tool(mcp_server, "hibob_get_workforce_form"))

    assert result["form"] == "position"
    assert result["submit_with"] == "hibob_create_position"
    assert any("required=true" in line for line in result["instructions"])
    assert "warnings" not in result

    assert [s["role"] for s in result["sections"]] == [
        "primary",
        "nested_required",
        "nested_optional",
    ]
    assert [s["argument"] for s in result["sections"]] == [
        "position_fields",
        "opening_fields",
        "budget_fields",
    ]

    sections = _sections(result)
    position = _fields(sections["position"])
    assert position["/position/department"]["options"] == [
        {"id": 10, "name": "Engineering"}
    ]
    assert position["/position/jobProfile"]["options"] == [
        {"id": 30, "name": "Engineer"}
    ]
    assert position["/position/department"]["required"] is True
    assert "unresolved_lists" not in sections["position"]
    assert [f["id"] for f in sections["position"]["read_only_fields"]] == [
        "/position/id"
    ]

    opening = _fields(sections["positionOpening"])
    assert opening["/positionOpening/recruitmentStatus"]["allowed_values"] == [
        "open",
        "onHold",
        "closed",
    ]
    budget = _fields(sections["positionBudget"])
    assert budget["/positionBudget/currency"]["options"] == [
        {"id": "GBP", "name": "GBP"}
    ]
    assert "Annual" in budget["/positionBudget/salaryPayPeriod"]["allowed_values"]

    # One combined fetch, then only the list the combined response lacked.
    assert all_lists.call_count == 1
    assert "includeArchived" not in str(all_lists.calls.last.request.url)
    assert job_profiles.call_count == 1


async def test_opening_form_fetches_only_what_it_needs(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    _mock_metadata(mock_api)
    all_lists = mock_api.get(NAMED_LISTS)

    result = json.loads(
        await call_tool(
            mcp_server, "hibob_get_workforce_form", {"object_type": "positionOpening"}
        )
    )

    assert result["submit_with"] == "hibob_create_position_opening"
    assert [s["object_type"] for s in result["sections"]] == ["positionOpening"]
    assert result["sections"][0]["argument"] == "fields"
    assert not mock_api.get(POSITION_META).called
    # No field referenced a named list, so none were fetched.
    assert not all_lists.called


async def test_form_passes_include_archived_through_to_every_list_call(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    _mock_metadata(mock_api)
    all_lists = mock_api.get(
        NAMED_LISTS, params__contains={"includeArchived": "true"}
    ).mock(return_value=httpx.Response(200, json=[]))
    by_name = mock_api.get(
        NAMED_LISTS + "/currency", params__contains={"includeArchived": "true"}
    ).mock(return_value=httpx.Response(200, json={"name": "currency", "items": []}))

    await call_tool(
        mcp_server,
        "hibob_get_workforce_form",
        {"object_type": "positionBudget", "include_archived_list_items": True},
    )

    assert all_lists.called
    assert by_name.called


async def test_form_survives_missing_lists_with_a_warning(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    _mock_metadata(mock_api)
    mock_api.get(NAMED_LISTS).mock(return_value=httpx.Response(200, json=[]))
    mock_api.get(f"{NAMED_LISTS}/currency").mock(
        return_value=httpx.Response(404, json={"error": "no such list"})
    )

    result = json.loads(
        await call_tool(
            mcp_server, "hibob_get_workforce_form", {"object_type": "positionBudget"}
        )
    )

    assert result["sections"][0]["unresolved_lists"] == ["currency"]
    assert any("'currency'" in warning for warning in result["warnings"])
    currency = _fields(result["sections"][0])["/positionBudget/currency"]
    assert currency["required"] is True
    assert "options" not in currency


async def test_form_treats_a_named_lists_outage_as_a_warning(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """A form without drop-down options still beats no form at all."""
    _mock_metadata(mock_api)
    mock_api.get(NAMED_LISTS).mock(return_value=httpx.Response(403, json={}))
    mock_api.get(f"{NAMED_LISTS}/currency").mock(
        return_value=httpx.Response(403, json={})
    )

    result = json.loads(
        await call_tool(
            mcp_server, "hibob_get_workforce_form", {"object_type": "positionBudget"}
        )
    )

    assert "sections" in result
    assert len(result["warnings"]) == 2


async def test_form_metadata_failure_is_an_error(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.get(POSITION_META).mock(return_value=httpx.Response(403, json={}))
    mock_api.get(OPENING_META).mock(
        return_value=httpx.Response(200, json=OPENING_METADATA)
    )
    mock_api.get(BUDGET_META).mock(
        return_value=httpx.Response(200, json=BUDGET_METADATA)
    )

    result = await call_tool(mcp_server, "hibob_get_workforce_form")

    assert result.startswith("Error:")
    assert "Manage positions" in result


async def test_form_accepts_named_lists_keyed_by_name(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    _mock_metadata(mock_api)
    mock_api.get(NAMED_LISTS).mock(
        return_value=httpx.Response(
            200,
            json={
                "currency": {
                    "name": "currency",
                    "items": [{"id": "EUR", "name": "EUR"}],
                }
            },
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server, "hibob_get_workforce_form", {"object_type": "positionBudget"}
        )
    )

    currency = _fields(result["sections"][0])["/positionBudget/currency"]
    assert currency["options"] == [{"id": "EUR", "name": "EUR"}]
