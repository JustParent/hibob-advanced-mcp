"""Write tool tests: payload envelopes, URLs and pre-flight validation."""

from __future__ import annotations

import json

import httpx
import respx
from conftest import call_tool
from mcp.server.fastmcp import FastMCP

POSITION_FIELDS = {
    "/position/effectiveDate": "2026-09-01",
    "/position/fte": 100,
    "/position/department": "Engineering",
    "/position/site": 123,
    "/position/jobProfile": 456,
}
OPENING_FIELDS = {"/positionOpening/expectedStartDate": "2026-09-30"}


async def test_create_position_sends_nested_envelope(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("/workforce-planning/positions").mock(
        return_value=httpx.Response(200, json={"id": 1, "positionOpeningId": 2})
    )

    result = await call_tool(
        mcp_server,
        "hibob_create_position",
        {
            "position_fields": POSITION_FIELDS,
            "opening_fields": OPENING_FIELDS,
            "budget_fields": {
                "/positionBudget/salaryPayPeriod": "Annual",
                "/positionBudget/currency": "GBP",
            },
        },
    )

    body = json.loads(route.calls.last.request.content)
    item = body["items"][0]
    assert item["objectType"] == "position"
    assert item["fields"]["/position/fte"] == {"value": 100}
    assert item["fields"]["/position/positionOpening"] == {
        "objectType": "positionOpening",
        "fields": {"/positionOpening/expectedStartDate": {"value": "2026-09-30"}},
    }
    assert item["fields"]["/position/positionBudget"]["fields"][
        "/positionBudget/currency"
    ] == {"value": "GBP"}
    assert json.loads(result) == {"id": 1, "positionOpeningId": 2}


async def test_create_position_omits_budget_when_not_given(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("/workforce-planning/positions").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )

    await call_tool(
        mcp_server,
        "hibob_create_position",
        {"position_fields": POSITION_FIELDS, "opening_fields": OPENING_FIELDS},
    )

    fields = json.loads(route.calls.last.request.content)["items"][0]["fields"]
    assert "/position/positionBudget" not in fields


async def test_create_position_validates_before_spending_rate_budget(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """Missing required fields must not cost one of ten write calls a minute."""
    route = mock_api.post("/workforce-planning/positions")

    result = await call_tool(
        mcp_server,
        "hibob_create_position",
        {
            "position_fields": {"/position/fte": 100},
            "opening_fields": OPENING_FIELDS,
        },
    )

    assert result.startswith("Error:")
    assert "/position/effectiveDate" in result
    assert not route.called


async def test_create_position_requires_opening_start_date(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("/workforce-planning/positions")

    result = await call_tool(
        mcp_server,
        "hibob_create_position",
        {"position_fields": POSITION_FIELDS, "opening_fields": {}},
    )

    assert "/positionOpening/expectedStartDate" in result
    assert not route.called


async def test_create_position_requires_currency_when_budget_supplied(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("/workforce-planning/positions")

    result = await call_tool(
        mcp_server,
        "hibob_create_position",
        {
            "position_fields": POSITION_FIELDS,
            "opening_fields": OPENING_FIELDS,
            "budget_fields": {"/positionBudget/salaryPayPeriod": "Annual"},
        },
    )

    assert "/positionBudget/currency" in result
    assert not route.called


async def test_update_position_patches_by_id(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.patch("/workforce-planning/positions/77").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    await call_tool(
        mcp_server,
        "hibob_update_position",
        {"position_id": "77", "fields": {"/position/fte": 50}},
    )

    assert json.loads(route.calls.last.request.content) == {
        "items": [
            {"objectType": "position", "fields": {"/position/fte": {"value": 50}}}
        ]
    }


async def test_update_position_rejects_non_updatable_field(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.patch("/workforce-planning/positions/77")

    result = await call_tool(
        mcp_server,
        "hibob_update_position",
        {"position_id": "77", "fields": {"/position/status": "vacant"}},
    )

    assert result.startswith("Error:")
    assert "/position/status" in result
    assert "/position/effectiveDate" in result  # lists what is updatable
    assert not route.called


async def test_update_position_requires_at_least_one_field(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.patch("/workforce-planning/positions/77")

    result = await call_tool(
        mcp_server, "hibob_update_position", {"position_id": "77", "fields": {}}
    )

    assert "at least one field" in result
    assert not route.called


async def test_cancel_position_sends_bodyless_patch(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.patch("/workforce-planning/positions/77/cancel").mock(
        return_value=httpx.Response(204)
    )

    result = await call_tool(
        mcp_server, "hibob_cancel_position", {"position_id": "77"}
    )

    assert not route.calls.last.request.content
    assert json.loads(result) == {"status": "cancelled", "positionId": "77"}


async def test_cancel_position_surfaces_filled_position_error(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.patch("/workforce-planning/positions/77/cancel").mock(
        return_value=httpx.Response(
            400, json={"key": "position_filled", "error": "Position is filled"}
        )
    )

    result = await call_tool(
        mcp_server, "hibob_cancel_position", {"position_id": "77"}
    )

    assert result.startswith("Error:")
    assert "Position is filled" in result


async def test_create_opening_posts_to_position_subresource(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("/workforce-planning/positions/5/position-openings").mock(
        return_value=httpx.Response(200, json={"id": 5, "positionOpeningId": 6})
    )

    await call_tool(
        mcp_server,
        "hibob_create_position_opening",
        {
            "position_id": "5",
            "fields": {
                "/positionOpening/expectedStartDate": "2026-10-01",
                "/positionOpening/recruitmentStatus": "open",
            },
        },
    )

    item = json.loads(route.calls.last.request.content)["items"][0]
    assert item["objectType"] == "positionOpening"
    assert item["fields"]["/positionOpening/recruitmentStatus"] == {"value": "open"}


async def test_create_opening_requires_expected_start_date(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("/workforce-planning/positions/5/position-openings")

    result = await call_tool(
        mcp_server,
        "hibob_create_position_opening",
        {"position_id": "5", "fields": {"/positionOpening/recruitmentStatus": "open"}},
    )

    assert "/positionOpening/expectedStartDate" in result
    assert not route.called


async def test_update_opening_targets_nested_url(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.patch(
        "/workforce-planning/positions/5/position-openings/6"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))

    await call_tool(
        mcp_server,
        "hibob_update_position_opening",
        {
            "position_id": "5",
            "opening_id": "6",
            "fields": {"/positionOpening/recruitmentStatus": "onHold"},
        },
    )

    assert route.called


async def test_delete_opening_targets_nested_url(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.delete(
        "/workforce-planning/positions/5/position-openings/6"
    ).mock(return_value=httpx.Response(204))

    result = await call_tool(
        mcp_server,
        "hibob_delete_position_opening",
        {"position_id": "5", "opening_id": "6"},
    )

    assert route.called
    assert json.loads(result)["status"] == "deleted"


async def test_create_budget_requires_pay_period_and_currency(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("/workforce-planning/positions/5/position-budget")

    result = await call_tool(
        mcp_server,
        "hibob_create_position_budget",
        {
            "position_id": "5",
            "fields": {"/positionBudget/expectedBaseSalaryCurrencyValue": 65000},
        },
    )

    assert result.startswith("Error:")
    assert "/positionBudget/currency" in result
    assert "/positionBudget/salaryPayPeriod" in result
    assert not route.called


async def test_create_budget_posts_envelope(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("/workforce-planning/positions/5/position-budget").mock(
        return_value=httpx.Response(200, json={"positionBudgetId": 8})
    )

    result = await call_tool(
        mcp_server,
        "hibob_create_position_budget",
        {
            "position_id": "5",
            "fields": {
                "/positionBudget/salaryPayPeriod": "Annual",
                "/positionBudget/currency": "GBP",
                "/positionBudget/expectedBaseSalaryCurrencyValue": 65000,
            },
        },
    )

    item = json.loads(route.calls.last.request.content)["items"][0]
    assert item["objectType"] == "positionBudget"
    assert json.loads(result) == {"positionBudgetId": 8}


async def test_update_budget_targets_nested_url(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.patch(
        "/workforce-planning/positions/5/position-budget/8"
    ).mock(return_value=httpx.Response(204))

    result = await call_tool(
        mcp_server,
        "hibob_update_position_budget",
        {
            "position_id": "5",
            "budget_id": "8",
            "fields": {"/positionBudget/expectedBaseSalaryCurrencyValue": 70000},
        },
    )

    assert route.called
    assert json.loads(result) == {"status": "updated"}


async def test_bare_field_names_are_accepted(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """Callers may omit the /position/ prefix."""
    route = mock_api.patch("/workforce-planning/positions/77").mock(
        return_value=httpx.Response(200, json={})
    )

    await call_tool(
        mcp_server,
        "hibob_update_position",
        {"position_id": "77", "fields": {"fte": 80}},
    )

    fields = json.loads(route.calls.last.request.content)["items"][0]["fields"]
    assert fields == {"/position/fte": {"value": 80}}
