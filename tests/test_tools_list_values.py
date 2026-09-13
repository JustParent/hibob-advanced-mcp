"""hibob_resolve_list_values: option names back to IDs, with the lookups cached.

After a form round trip a bot may hold only the option names the user picked
("Madrid, Lisbon") and the field's label, having lost both the list ID and
the option IDs. This tool takes exactly those two things and returns the IDs.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx
from mcp.server.fastmcp import FastMCP

from conftest import call_tool

POSITION_META = "/metadata/objects/position"
NAMED_LISTS = "/company/named-lists"
LIST_ID = "entity_multi_list_1784129026359"


def _meta(
    field_id: str, name: str, field_type: str, list_id: str | None = None
) -> dict:
    descriptor: dict[str, Any] = {
        "id": field_id,
        "name": name,
        "fieldType": {"type": field_type},
    }
    if list_id:
        descriptor["fieldType"]["typeData"] = {"listId": list_id}
    return descriptor


METADATA = [
    _meta("/position/site", "Site", "list_id", "site"),
    _meta("/position/field_24133483", "Locations for hiring", "multi_list", LIST_ID),
    _meta("/position/field_24285464", "Hiring Manager email", "text"),
]
LOCATIONS = {
    "name": LIST_ID,
    "values": [
        {"id": "267873068", "name": "Lisbon", "value": "Lisbon"},
        {"id": "267873081", "name": "Madrid", "value": "Madrid"},
        {"id": "267873079", "name": "Spain", "value": "Spain"},
    ],
}


def _mock(mock_api: respx.MockRouter) -> tuple[respx.Route, respx.Route]:
    metadata = mock_api.get(POSITION_META).mock(
        return_value=httpx.Response(200, json=METADATA)
    )
    named = mock_api.get(f"{NAMED_LISTS}/{LIST_ID}").mock(
        return_value=httpx.Response(200, json=LOCATIONS)
    )
    return metadata, named


async def test_names_for_a_field_given_by_label_come_back_as_ids(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    _mock(mock_api)

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_resolve_list_values",
            {"field": "Locations for hiring", "values": ["Madrid", "Lisbon"]},
        )
    )

    assert result["field_id"] == "/position/field_24133483"
    assert result["field_name"] == "Locations for hiring"
    assert result["field_type"] == "multi_list"
    assert result["list_id"] == LIST_ID
    assert result["resolved"] == {"Madrid": "267873081", "Lisbon": "267873068"}
    assert result["values"] == ["267873081", "267873068"]
    assert result["complete"] is True
    assert result["multi"] is True
    assert "/position/field_24133483" in result["submit"]


async def test_the_field_may_be_given_by_id(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    _mock(mock_api)

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_resolve_list_values",
            {"field": "/position/field_24133483", "values": ["Spain"]},
        )
    )

    assert result["values"] == ["267873079"]


async def test_metadata_and_the_list_are_fetched_once_across_calls(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """Both endpoints allow 50 calls a minute; a bot resolving field after
    field must not spend one of each every time."""
    metadata, named = _mock(mock_api)

    for values in (["Madrid"], ["Lisbon"], ["Spain"]):
        await call_tool(
            mcp_server,
            "hibob_resolve_list_values",
            {"field": "Locations for hiring", "values": values},
        )

    assert metadata.call_count == 1
    assert named.call_count == 1


async def test_the_form_tool_shares_the_metadata_cache(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    metadata, _ = _mock(mock_api)
    mock_api.get(f"{NAMED_LISTS}/site").mock(
        return_value=httpx.Response(200, json={"name": "site", "items": []})
    )
    mock_api.get("/positions/position-openings/metadata").mock(
        return_value=httpx.Response(200, json=[])
    )
    mock_api.get("/positions/position-budget/metadata").mock(
        return_value=httpx.Response(200, json=[])
    )

    await call_tool(mcp_server, "hibob_get_workforce_form", {"object_type": "position"})
    await call_tool(
        mcp_server,
        "hibob_resolve_list_values",
        {"field": "Locations for hiring", "values": ["Madrid"]},
    )

    assert metadata.call_count == 1


async def test_a_failed_metadata_fetch_is_not_cached(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """A 500 is not retried by the read client, so the first call fails
    outright; the cache must not remember that failure."""
    metadata = mock_api.get(POSITION_META).mock(
        side_effect=[httpx.Response(500, json={}), httpx.Response(200, json=METADATA)]
    )
    mock_api.get(f"{NAMED_LISTS}/{LIST_ID}").mock(
        return_value=httpx.Response(200, json=LOCATIONS)
    )

    first = await call_tool(
        mcp_server,
        "hibob_resolve_list_values",
        {"field": "Locations for hiring", "values": ["Madrid"]},
    )
    second = json.loads(
        await call_tool(
            mcp_server,
            "hibob_resolve_list_values",
            {"field": "Locations for hiring", "values": ["Madrid"]},
        )
    )

    assert first.startswith("Error:")
    assert second["values"] == ["267873081"]
    assert metadata.call_count >= 2


async def test_unmatched_names_come_back_with_candidates_not_an_error(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    _mock(mock_api)

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_resolve_list_values",
            {"field": "Locations for hiring", "values": ["Madrid", "Lisboa"]},
        )
    )

    assert result["complete"] is False
    assert result["values"] == ["267873081"]
    assert result["unmatched"][0]["name"] == "Lisboa"
    assert result["unmatched"][0]["candidates"]


async def test_a_text_field_is_refused_before_any_list_is_fetched(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    _, named = _mock(mock_api)

    result = await call_tool(
        mcp_server,
        "hibob_resolve_list_values",
        {"field": "Hiring Manager email", "values": ["ermis@pleo.io"]},
    )

    assert result.startswith("Error:")
    assert "text field" in result
    assert not named.called


async def test_an_unknown_field_lists_the_list_backed_ones(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    _mock(mock_api)

    result = await call_tool(
        mcp_server,
        "hibob_resolve_list_values",
        {"field": "Locations", "values": ["Madrid"]},
    )

    assert result.startswith("Error:")
    assert "Locations for hiring" in result and "Site" in result


async def test_archived_items_are_fetched_only_when_asked(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.get(POSITION_META).mock(return_value=httpx.Response(200, json=METADATA))
    archived = mock_api.get(
        f"{NAMED_LISTS}/{LIST_ID}", params__contains={"includeArchived": "true"}
    ).mock(return_value=httpx.Response(200, json=LOCATIONS))
    live = mock_api.get(f"{NAMED_LISTS}/{LIST_ID}").mock(
        return_value=httpx.Response(200, json=LOCATIONS)
    )

    await call_tool(
        mcp_server,
        "hibob_resolve_list_values",
        {"field": "Locations for hiring", "values": ["Madrid"]},
    )
    await call_tool(
        mcp_server,
        "hibob_resolve_list_values",
        {
            "field": "Locations for hiring",
            "values": ["Madrid"],
            "include_archived_list_items": True,
        },
    )

    assert live.call_count == 1
    assert archived.call_count == 1


async def test_a_single_value_list_field_says_to_submit_one_id(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.get(POSITION_META).mock(return_value=httpx.Response(200, json=METADATA))
    mock_api.get(f"{NAMED_LISTS}/site").mock(
        return_value=httpx.Response(
            200, json={"name": "site", "items": [{"id": 20, "name": "Berlin - Office"}]}
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_resolve_list_values",
            {"field": "Site", "values": ["berlin - office"]},
        )
    )

    assert result["values"] == ["20"]
    assert result["multi"] is False
    assert "/position/site" in result["submit"]


async def test_the_form_points_list_fields_at_this_tool(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.get(POSITION_META).mock(return_value=httpx.Response(200, json=METADATA))
    mock_api.get(f"{NAMED_LISTS}/site").mock(
        return_value=httpx.Response(200, json={"name": "site", "items": []})
    )
    mock_api.get(f"{NAMED_LISTS}/{LIST_ID}").mock(
        return_value=httpx.Response(200, json=LOCATIONS)
    )
    mock_api.get("/positions/position-openings/metadata").mock(
        return_value=httpx.Response(200, json=[])
    )
    mock_api.get("/positions/position-budget/metadata").mock(
        return_value=httpx.Response(200, json=[])
    )

    result = json.loads(
        await call_tool(
            mcp_server, "hibob_get_workforce_form", {"object_type": "position"}
        )
    )

    fields = {f["id"]: f for f in result["sections"][0]["fields"]}
    assert (
        fields["/position/field_24133483"]["resolve_with"]
        == "hibob_resolve_list_values"
    )
    assert "resolve_with" not in fields["/position/field_24285464"]
    assert any("hibob_resolve_list_values" in line for line in result["instructions"])
