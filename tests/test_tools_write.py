"""Write tool tests: payload envelopes, URLs and pre-flight validation."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx
from mcp.server.fastmcp import FastMCP

from conftest import call_tool

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
    created = json.loads(result)
    assert created["id"] == 1 and created["positionOpeningId"] == 2


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

    result = await call_tool(mcp_server, "hibob_cancel_position", {"position_id": "77"})

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

    result = await call_tool(mcp_server, "hibob_cancel_position", {"position_id": "77"})

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
    route = mock_api.patch("/workforce-planning/positions/5/position-openings/6").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

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
    route = mock_api.delete("/workforce-planning/positions/5/position-openings/6").mock(
        return_value=httpx.Response(204)
    )

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
    assert json.loads(result)["positionBudgetId"] == 8


async def test_update_budget_targets_nested_url(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.patch("/workforce-planning/positions/5/position-budget/8").mock(
        return_value=httpx.Response(204)
    )

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
    assert json.loads(result)["status"] == "updated"


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


# ------------------------------------------------------ verification after writes


POSITION_SEARCH = "/objects/position/search"
OPENING_SEARCH = "/positions/position-openings/search"
BUDGET_SEARCH = "/positions/position-budget/search"


def _position_row(position_id: int, **extra: object) -> dict[str, Any]:
    row: dict[str, Any] = {
        "/position/id": {"value": position_id, "humanReadable": str(position_id)},
        "/position/name": {"value": "P-1", "humanReadable": "P-1"},
    }
    row.update({key: {"value": value} for key, value in extra.items()})
    return row


def _opening_row(opening_id: int, position_id: int, **extra: object) -> dict[str, Any]:
    row: dict[str, Any] = {
        "/positionOpening/id": {"value": opening_id},
        "/positionOpening/positionId": {"value": position_id},
    }
    row.update({key: {"value": value} for key, value in extra.items()})
    return row


async def test_create_position_reads_back_the_position_and_its_opening(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post("/workforce-planning/positions").mock(
        return_value=httpx.Response(200, json={"id": 1, "positionOpeningId": 2})
    )
    positions = mock_api.post(POSITION_SEARCH).mock(
        return_value=httpx.Response(200, json=[_position_row(1, **POSITION_FIELDS)])
    )
    openings = mock_api.post(OPENING_SEARCH).mock(
        return_value=httpx.Response(
            200, json={"values": [_opening_row(2, 1, **OPENING_FIELDS)]}
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_create_position",
            {"position_fields": POSITION_FIELDS, "opening_fields": OPENING_FIELDS},
        )
    )

    assert result["id"] == 1 and result["positionOpeningId"] == 2
    assert result["verified"] is True
    assert result["position"]["values"]["/position/fte"] == 100
    assert result["opening"]["values"]["/positionOpening/positionId"] == 1
    position_body = json.loads(positions.calls.last.request.content)
    assert position_body["filters"] == [
        {"fieldId": "/position/id", "operator": "equals", "values": ["1"]}
    ]
    # The fields that were written are read back, alongside the basics.
    assert set(POSITION_FIELDS) <= set(position_body["fields"])
    assert json.loads(openings.calls.last.request.content)["filters"] == [
        {"fieldId": "/positionOpening/id", "operator": "equals", "values": ["2"]}
    ]


async def test_create_position_reports_a_failed_read_back_without_hiding_the_ids(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """The write succeeded; a verification failure must not look like a failed write."""
    mock_api.post("/workforce-planning/positions").mock(
        return_value=httpx.Response(200, json={"id": 1, "positionOpeningId": 2})
    )
    mock_api.post(POSITION_SEARCH).mock(return_value=httpx.Response(500, json={}))
    mock_api.post(OPENING_SEARCH).mock(
        return_value=httpx.Response(
            200, json={"values": [_opening_row(2, 1, **OPENING_FIELDS)]}
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_create_position",
            {"position_fields": POSITION_FIELDS, "opening_fields": OPENING_FIELDS},
        )
    )

    assert result["id"] == 1 and result["positionOpeningId"] == 2
    assert result["verified"] is False
    assert "server error" in result["verification_error"]
    assert "position" not in result
    assert result["opening"]["values"]["/positionOpening/id"] == 2


async def test_create_opening_reads_back_and_confirms_the_parent(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post("/workforce-planning/positions/5/position-openings").mock(
        return_value=httpx.Response(200, json={"id": 5, "positionOpeningId": 6})
    )
    openings = mock_api.post(OPENING_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    _opening_row(
                        6,
                        5,
                        **{
                            "/positionOpening/expectedStartDate": "2026-10-01",
                            "/positionOpening/recruitmentStatus": "open",
                        },
                    )
                ]
            },
        )
    )

    result = json.loads(
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
    )

    assert result["positionOpeningId"] == 6
    assert result["verified"] is True
    assert result["opening"]["values"]["/positionOpening/recruitmentStatus"] == "open"
    body = json.loads(openings.calls.last.request.content)
    assert body["filters"] == [
        {"fieldId": "/positionOpening/id", "operator": "equals", "values": ["6"]}
    ]
    assert "/positionOpening/recruitmentStatus" in body["fields"]


async def test_create_opening_flags_a_parent_mismatch(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post("/workforce-planning/positions/5/position-openings").mock(
        return_value=httpx.Response(200, json={"id": 5, "positionOpeningId": 6})
    )
    mock_api.post(OPENING_SEARCH).mock(
        return_value=httpx.Response(
            200, json={"values": [_opening_row(6, 99, **OPENING_FIELDS)]}
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_create_position_opening",
            {"position_id": "5", "fields": OPENING_FIELDS},
        )
    )

    assert result["verified"] is False
    assert "99" in result["verification_error"]


async def test_create_budget_reads_back_the_budget(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post("/workforce-planning/positions/5/position-budget").mock(
        return_value=httpx.Response(200, json={"positionBudgetId": 8})
    )
    budgets = mock_api.post(BUDGET_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {
                        "/positionBudget/id": {"value": 8},
                        "/positionBudget/positionId": {"value": 5},
                        "/positionBudget/currency": {"value": "GBP"},
                        "/positionBudget/salaryPayPeriod": {"value": "Annual"},
                    }
                ]
            },
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_create_position_budget",
            {
                "position_id": "5",
                "fields": {
                    "/positionBudget/salaryPayPeriod": "Annual",
                    "/positionBudget/currency": "GBP",
                },
            },
        )
    )

    assert result["positionBudgetId"] == 8
    assert result["verified"] is True
    assert result["budget"]["values"]["/positionBudget/currency"] == "GBP"
    assert json.loads(budgets.calls.last.request.content)["filters"] == [
        {"fieldId": "/positionBudget/id", "operator": "equals", "values": ["8"]}
    ]


async def test_update_position_reads_back_the_changed_fields(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.patch("/workforce-planning/positions/77").mock(
        return_value=httpx.Response(204)
    )
    positions = mock_api.post(POSITION_SEARCH).mock(
        return_value=httpx.Response(
            200, json=[_position_row(77, **{"/position/fte": 50})]
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_update_position",
            {"position_id": "77", "fields": {"/position/fte": 50}},
        )
    )

    assert result["status"] == "updated"
    assert result["verified"] is True
    assert result["position"]["values"]["/position/fte"] == 50
    assert "/position/fte" in json.loads(positions.calls.last.request.content)["fields"]


async def test_update_opening_reads_back_the_opening(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.patch("/workforce-planning/positions/5/position-openings/6").mock(
        return_value=httpx.Response(204)
    )
    mock_api.post(OPENING_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    _opening_row(
                        6, 5, **{"/positionOpening/recruitmentStatus": "onHold"}
                    )
                ]
            },
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_update_position_opening",
            {
                "position_id": "5",
                "opening_id": "6",
                "fields": {"/positionOpening/recruitmentStatus": "onHold"},
            },
        )
    )

    assert result["status"] == "updated"
    assert result["verified"] is True
    assert result["opening"]["values"]["/positionOpening/recruitmentStatus"] == "onHold"


async def test_update_budget_reads_back_the_budget(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.patch("/workforce-planning/positions/5/position-budget/8").mock(
        return_value=httpx.Response(204)
    )
    mock_api.post(BUDGET_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {
                        "/positionBudget/id": {"value": 8},
                        "/positionBudget/expectedBaseSalaryCurrencyValue": {
                            "value": 70000
                        },
                    }
                ]
            },
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_update_position_budget",
            {
                "position_id": "5",
                "budget_id": "8",
                "fields": {"/positionBudget/expectedBaseSalaryCurrencyValue": 70000},
            },
        )
    )

    assert result["status"] == "updated"
    assert result["verified"] is True
    assert (
        result["budget"]["values"]["/positionBudget/expectedBaseSalaryCurrencyValue"]
        == 70000
    )


# ------------------------------------------------ custom fields on updates


CUSTOM_LOCATIONS = "/position/field_24133483"


async def test_update_position_accepts_custom_fields_and_says_they_are_undocumented(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    patch = mock_api.patch("/workforce-planning/positions/77").mock(
        return_value=httpx.Response(204)
    )
    mock_api.post(POSITION_SEARCH).mock(
        return_value=httpx.Response(
            200, json=[_position_row(77, **{CUSTOM_LOCATIONS: ["10", "11"]})]
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_update_position",
            {"position_id": "77", "fields": {CUSTOM_LOCATIONS: [10, 11]}},
        )
    )

    assert json.loads(patch.calls.last.request.content)["items"][0]["fields"] == {
        CUSTOM_LOCATIONS: {"value": [10, 11]}
    }
    assert result["status"] == "updated"
    assert result["undocumented_fields"] == [CUSTOM_LOCATIONS]
    assert result["verified"] is True
    assert "unconfirmed_fields" not in result


async def test_update_position_flags_a_custom_field_hibob_did_not_keep(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """HiBob may accept the PATCH and ignore a field it cannot write; the
    read-back is what reveals that, and the caller must be told."""
    mock_api.patch("/workforce-planning/positions/77").mock(
        return_value=httpx.Response(204)
    )
    mock_api.post(POSITION_SEARCH).mock(
        return_value=httpx.Response(
            200, json=[_position_row(77, **{"/position/fte": 50})]
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_update_position",
            {
                "position_id": "77",
                "fields": {"/position/fte": 50, CUSTOM_LOCATIONS: ["10"]},
            },
        )
    )

    assert result["status"] == "updated"
    assert result["verified"] is False
    assert result["unconfirmed_fields"] == {
        CUSTOM_LOCATIONS: {"sent": ["10"], "read_back": None}
    }
    assert CUSTOM_LOCATIONS in result["verification_error"]
    assert "custom" in result["verification_error"].lower()
    assert result["position"]["values"]["/position/fte"] == 50


async def test_update_position_explains_a_rejected_custom_field(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.patch("/workforce-planning/positions/77").mock(
        return_value=httpx.Response(400, json={"error": "Unknown field"})
    )

    result = await call_tool(
        mcp_server,
        "hibob_update_position",
        {"position_id": "77", "fields": {CUSTOM_LOCATIONS: ["10"]}},
    )

    assert result.startswith("Error:")
    assert "Unknown field" in result
    assert CUSTOM_LOCATIONS in result
    assert "custom" in result.lower()


async def test_update_position_still_refuses_fields_hibob_sets_itself(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.patch("/workforce-planning/positions/77")

    result = await call_tool(
        mcp_server,
        "hibob_update_position",
        {"position_id": "77", "fields": {"/position/filledBy": "123"}},
    )

    assert result.startswith("Error:")
    assert "/position/filledBy" in result
    assert not route.called


async def test_update_opening_flags_a_value_that_did_not_stick(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.patch("/workforce-planning/positions/5/position-openings/6").mock(
        return_value=httpx.Response(204)
    )
    mock_api.post(OPENING_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    _opening_row(6, 5, **{"/positionOpening/recruitmentStatus": "open"})
                ]
            },
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_update_position_opening",
            {
                "position_id": "5",
                "opening_id": "6",
                "fields": {"/positionOpening/recruitmentStatus": "onHold"},
            },
        )
    )

    assert result["verified"] is False
    assert result["unconfirmed_fields"] == {
        "/positionOpening/recruitmentStatus": {"sent": "onHold", "read_back": "open"}
    }


async def test_read_back_comparison_tolerates_hibob_value_shapes(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """Numbers as strings, money as {"value", "currency"}, labels in another
    case: none of these is a mismatch."""
    mock_api.patch("/workforce-planning/positions/5/position-budget/8").mock(
        return_value=httpx.Response(204)
    )
    mock_api.post(BUDGET_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {
                        "/positionBudget/id": {"value": 8},
                        "/positionBudget/expectedBaseSalaryCurrencyValue": {
                            "value": {"value": 70000.0, "currency": "GBP"}
                        },
                        "/positionBudget/salaryPayPeriod": {"value": "annual"},
                        "/positionBudget/currency": {"value": "GBP"},
                    }
                ]
            },
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_update_position_budget",
            {
                "position_id": "5",
                "budget_id": "8",
                "fields": {
                    "/positionBudget/expectedBaseSalaryCurrencyValue": "70000",
                    "/positionBudget/salaryPayPeriod": "Annual",
                    "currency": "GBP",
                },
            },
        )
    )

    assert result["verified"] is True
    assert "unconfirmed_fields" not in result
