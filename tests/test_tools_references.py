"""Write and lookup tools addressed by position and opening names.

A position may be given as HiBob shows it ("P-0000000368") rather than by
its numeric ID, and an opening likewise ("O-6853240227"). Names are resolved
through one search before anything is written, and a write under the wrong
parent is refused rather than sent.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx
from mcp.server.fastmcp import FastMCP

from conftest import call_tool

POSITION_SEARCH = "/objects/position/search"
OPENING_SEARCH = "/positions/position-openings/search"
BUDGET_SEARCH = "/positions/position-budget/search"

STEPHANIE = 2251800820033996  # P-0000000368
STEPHANIE_BUDGET = 2251800820033995
WRONG = 2251800820165939  # P-0000000910
WRONG_BUDGET = 2251800820165938
OPENING = 2251800820173708  # O-6853240227, under P-0000000368


def _position(position_id: int, name: str, budget: int | None, **extra: Any) -> dict:
    row: dict[str, Any] = {
        "/position/id": {"value": position_id, "humanReadable": str(position_id)},
        "/position/name": {"value": name, "humanReadable": name},
    }
    if budget is not None:
        row["/position/budget"] = {"value": budget, "humanReadable": str(budget)}
    row.update({key: {"value": value} for key, value in extra.items()})
    return row


def _opening(opening_id: int, name: str, position_id: int, **extra: Any) -> dict:
    row: dict[str, Any] = {
        "/positionOpening/id": {"value": opening_id},
        "/positionOpening/positionOpeningName": {"value": name},
        "/positionOpening/positionId": {"value": position_id},
    }
    row.update({key: {"value": value} for key, value in extra.items()})
    return row


def _by_filter(**responses: list[dict]) -> Any:
    """Answer a search by the value of its (single) filter clause."""

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        clause = body["filters"][0]
        key = f"{clause['fieldId']}={'|'.join(clause['values'])}"
        for pattern, rows in responses.items():
            if pattern == key:
                if "pagination" in body or "positionOpening" in clause["fieldId"]:
                    return httpx.Response(200, json={"values": rows})
                return httpx.Response(200, json=rows)
        return httpx.Response(200, json={"values": []} if "pagination" in body else [])

    return respond


def _positions(**responses: list[dict]) -> Any:
    return _by_filter(**{k.replace("__", "/"): v for k, v in responses.items()})


# ------------------------------------------------------------- positions


async def test_update_position_by_name_resolves_it_first(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    search = mock_api.post(POSITION_SEARCH).mock(
        side_effect=_by_filter(
            **{
                "/position/name=P-0000000368": [
                    _position(STEPHANIE, "P-0000000368", None)
                ],
                f"/position/id={STEPHANIE}": [
                    _position(STEPHANIE, "P-0000000368", None, **{"/position/fte": 50})
                ],
            }
        )
    )
    patch = mock_api.patch(f"/workforce-planning/positions/{STEPHANIE}").mock(
        return_value=httpx.Response(204)
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_update_position",
            {"position_id": " P-0000000368 ", "fields": {"/position/fte": 50}},
        )
    )

    assert patch.called
    assert result["position_id"] == str(STEPHANIE)
    assert result["verified"] is True
    first = json.loads(search.calls[0].request.content)
    assert first["filters"] == [
        {"fieldId": "/position/name", "operator": "equals", "values": ["P-0000000368"]}
    ]


async def test_update_position_by_id_does_not_look_it_up_first(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    search = mock_api.post(POSITION_SEARCH).mock(
        return_value=httpx.Response(200, json=[_position(77, "P-77", None)])
    )
    mock_api.patch("/workforce-planning/positions/77").mock(
        return_value=httpx.Response(204)
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_update_position",
            {"position_id": "77", "fields": {"/position/fte": 50}},
        )
    )

    assert result["position_id"] == "77"
    assert search.call_count == 1  # the read-back only


async def test_update_position_with_an_unknown_name_writes_nothing(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post(POSITION_SEARCH).mock(return_value=httpx.Response(200, json=[]))
    patch = mock_api.patch("/workforce-planning/positions/1")

    result = await call_tool(
        mcp_server,
        "hibob_update_position",
        {"position_id": "P-0000009999", "fields": {"/position/fte": 50}},
    )

    assert result.startswith("Error:")
    assert "No position named 'P-0000009999'" in result
    assert not patch.called


async def test_cancel_position_by_name(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post(POSITION_SEARCH).mock(
        return_value=httpx.Response(200, json=[_position(77, "P-77", None)])
    )
    route = mock_api.patch("/workforce-planning/positions/77/cancel").mock(
        return_value=httpx.Response(204)
    )

    result = json.loads(
        await call_tool(mcp_server, "hibob_cancel_position", {"position_id": "P-77"})
    )

    assert route.called
    assert result == {"status": "cancelled", "positionId": "77"}


# -------------------------------------------------------------- openings


async def test_update_opening_by_names_resolves_both_and_checks_the_parent(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post(POSITION_SEARCH).mock(
        return_value=httpx.Response(
            200, json=[_position(STEPHANIE, "P-0000000368", None)]
        )
    )
    openings = mock_api.post(OPENING_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    _opening(
                        OPENING,
                        "O-6853240227",
                        STEPHANIE,
                        **{"/positionOpening/expectedStartDate": "2026-12-01"},
                    )
                ]
            },
        )
    )
    patch = mock_api.patch(
        f"/workforce-planning/positions/{STEPHANIE}/position-openings/{OPENING}"
    ).mock(return_value=httpx.Response(204))

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_update_position_opening",
            {
                "position_id": "P-0000000368",
                "opening_id": "O-6853240227",
                "fields": {"/positionOpening/expectedStartDate": "2026-12-01"},
            },
        )
    )

    assert patch.called
    assert result["verified"] is True
    assert result["position_id"] == str(STEPHANIE)
    assert result["opening_id"] == str(OPENING)
    lookup = json.loads(openings.calls[0].request.content)
    assert lookup["filters"] == [
        {
            "fieldId": "/positionOpening/positionOpeningName",
            "operator": "equals",
            "values": ["O-6853240227"],
        }
    ]
    assert set(lookup["fields"]) >= {
        "/positionOpening/id",
        "/positionOpening/positionOpeningName",
        "/positionOpening/positionId",
    }


async def test_update_opening_under_the_wrong_position_is_refused(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """The incident: the right opening name, the wrong position ID."""
    mock_api.post(OPENING_SEARCH).mock(
        return_value=httpx.Response(
            200, json={"values": [_opening(OPENING, "O-6853240227", STEPHANIE)]}
        )
    )
    patch = mock_api.patch(
        f"/workforce-planning/positions/{WRONG}/position-openings/{OPENING}"
    )

    result = await call_tool(
        mcp_server,
        "hibob_update_position_opening",
        {
            "position_id": str(WRONG),
            "opening_id": "O-6853240227",
            "fields": {"/positionOpening/expectedStartDate": "2026-12-01"},
        },
    )

    assert result.startswith("Error:")
    assert f"belongs to position {STEPHANIE}" in result
    assert f"not position {WRONG}" in result
    assert "Nothing was written" in result
    assert not patch.called


async def test_update_opening_by_id_is_still_checked_against_its_position(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    openings = mock_api.post(OPENING_SEARCH).mock(
        return_value=httpx.Response(200, json={"values": [_opening(6, "O-6", 99)]})
    )
    patch = mock_api.patch("/workforce-planning/positions/5/position-openings/6")

    result = await call_tool(
        mcp_server,
        "hibob_update_position_opening",
        {
            "position_id": "5",
            "opening_id": "6",
            "fields": {"/positionOpening/recruitmentStatus": "onHold"},
        },
    )

    assert result.startswith("Error:")
    assert "belongs to position 99, not position 5" in result
    assert not patch.called
    assert json.loads(openings.calls[0].request.content)["filters"] == [
        {"fieldId": "/positionOpening/id", "operator": "equals", "values": ["6"]}
    ]


async def test_delete_opening_under_the_wrong_position_is_refused(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post(OPENING_SEARCH).mock(
        return_value=httpx.Response(200, json={"values": [_opening(6, "O-6", 99)]})
    )
    delete = mock_api.delete("/workforce-planning/positions/5/position-openings/6")

    result = await call_tool(
        mcp_server,
        "hibob_delete_position_opening",
        {"position_id": "5", "opening_id": "O-6"},
    )

    assert result.startswith("Error:")
    assert "Nothing was written" in result
    assert not delete.called


async def test_delete_opening_by_names(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post(POSITION_SEARCH).mock(
        return_value=httpx.Response(200, json=[_position(5, "P-5", None)])
    )
    mock_api.post(OPENING_SEARCH).mock(
        return_value=httpx.Response(200, json={"values": [_opening(6, "O-6", 5)]})
    )
    delete = mock_api.delete(
        "/workforce-planning/positions/5/position-openings/6"
    ).mock(return_value=httpx.Response(204))

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_delete_position_opening",
            {"position_id": "P-5", "opening_id": "O-6"},
        )
    )

    assert delete.called
    assert result == {"status": "deleted", "positionId": "5", "positionOpeningId": "6"}


async def test_create_opening_under_a_position_named_by_its_label(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post(POSITION_SEARCH).mock(
        return_value=httpx.Response(200, json=[_position(5, "P-5", None)])
    )
    post = mock_api.post("/workforce-planning/positions/5/position-openings").mock(
        return_value=httpx.Response(200, json={"id": 5, "positionOpeningId": 6})
    )
    mock_api.post(OPENING_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    _opening(
                        6,
                        "O-6",
                        5,
                        **{"/positionOpening/expectedStartDate": "2026-12-01"},
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
                "position_id": "P-5",
                "fields": {"/positionOpening/expectedStartDate": "2026-12-01"},
            },
        )
    )

    assert post.called
    assert result["verified"] is True
    assert result["position_id"] == "5"


# --------------------------------------------------------------- budgets


async def test_update_budget_needs_no_budget_id_when_the_position_is_known(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post(POSITION_SEARCH).mock(
        return_value=httpx.Response(
            200, json=[_position(STEPHANIE, "P-0000000368", STEPHANIE_BUDGET)]
        )
    )
    patch = mock_api.patch(
        f"/workforce-planning/positions/{STEPHANIE}/position-budget/{STEPHANIE_BUDGET}"
    ).mock(return_value=httpx.Response(204))
    mock_api.post(BUDGET_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {
                        "/positionBudget/id": {"value": STEPHANIE_BUDGET},
                        "/positionBudget/expectedBaseSalaryCurrencyValue": {
                            "value": {"value": 200000, "currency": "EUR"}
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
                "position_id": "P-0000000368",
                "fields": {"/positionBudget/expectedBaseSalaryCurrencyValue": 200000},
            },
        )
    )

    assert patch.called
    assert result["position_id"] == str(STEPHANIE)
    assert result["budget_id"] == str(STEPHANIE_BUDGET)
    assert result["verified"] is True


async def test_update_budget_with_another_positions_budget_is_refused(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """The incident: P-0000000910's ID with P-0000000368's budget."""
    mock_api.post(POSITION_SEARCH).mock(
        return_value=httpx.Response(
            200, json=[_position(WRONG, "P-0000000910", WRONG_BUDGET)]
        )
    )
    patch = mock_api.patch(
        f"/workforce-planning/positions/{WRONG}/position-budget/{STEPHANIE_BUDGET}"
    )

    result = await call_tool(
        mcp_server,
        "hibob_update_position_budget",
        {
            "position_id": str(WRONG),
            "budget_id": str(STEPHANIE_BUDGET),
            "fields": {"/positionBudget/expectedBaseSalaryCurrencyValue": 200000},
        },
    )

    assert result.startswith("Error:")
    assert (
        f"Budget {STEPHANIE_BUDGET} does not belong to position P-0000000910" in result
    )
    assert f"whose budget is {WRONG_BUDGET}" in result
    assert not patch.called


async def test_update_budget_on_a_position_without_one_is_refused(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post(POSITION_SEARCH).mock(
        return_value=httpx.Response(200, json=[_position(5, "P-5", None)])
    )
    patch = mock_api.patch("/workforce-planning/positions/5/position-budget/8")

    result = await call_tool(
        mcp_server,
        "hibob_update_position_budget",
        {
            "position_id": "5",
            "budget_id": "8",
            "fields": {"/positionBudget/currency": "EUR"},
        },
    )

    assert "has no budget to update" in result
    assert not patch.called


async def test_create_budget_on_a_position_that_has_one_is_refused(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post(POSITION_SEARCH).mock(
        return_value=httpx.Response(200, json=[_position(5, "P-5", 8)])
    )
    post = mock_api.post("/workforce-planning/positions/5/position-budget")

    result = await call_tool(
        mcp_server,
        "hibob_create_position_budget",
        {
            "position_id": "P-5",
            "fields": {
                "/positionBudget/salaryPayPeriod": "Annual",
                "/positionBudget/currency": "EUR",
            },
        },
    )

    assert "already has budget 8" in result
    assert "hibob_update_position_budget" in result
    assert not post.called


async def test_create_budget_by_position_name(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post(POSITION_SEARCH).mock(
        return_value=httpx.Response(200, json=[_position(5, "P-5", None)])
    )
    post = mock_api.post("/workforce-planning/positions/5/position-budget").mock(
        return_value=httpx.Response(200, json={"positionBudgetId": 8})
    )
    mock_api.post(BUDGET_SEARCH).mock(
        return_value=httpx.Response(
            200, json={"values": [{"/positionBudget/id": {"value": 8}}]}
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_create_position_budget",
            {
                "position_id": "P-5",
                "fields": {
                    "/positionBudget/salaryPayPeriod": "Annual",
                    "/positionBudget/currency": "EUR",
                },
            },
        )
    )

    assert post.called
    assert result["positionBudgetId"] == 8
    assert result["position_id"] == "5"


# --------------------------------------------------------------- lookups


async def test_openings_for_positions_accepts_position_names(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    positions = mock_api.post(POSITION_SEARCH).mock(
        return_value=httpx.Response(200, json=[_position(7, "P-7", None)])
    )
    mock_api.post(OPENING_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [_opening(1, "O-1", 7), _opening(2, "O-2", 8)],
                "response_metadata": {"next_cursor": None},
            },
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_get_openings_for_positions",
            {"position_ids": ["P-7", "8"]},
        )
    )

    assert result["count"] == 2
    assert result["counts_by_position"] == {"7": 1, "8": 1}
    assert result["resolved_positions"] == {"P-7": "7"}
    assert json.loads(positions.calls[0].request.content)["filters"] == [
        {"fieldId": "/position/name", "operator": "equals", "values": ["P-7"]}
    ]


async def test_openings_for_positions_names_an_unknown_position(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    mock_api.post(POSITION_SEARCH).mock(return_value=httpx.Response(200, json=[]))
    openings = mock_api.post(OPENING_SEARCH)

    result = await call_tool(
        mcp_server, "hibob_get_openings_for_positions", {"position_ids": ["P-404"]}
    )

    assert "No position named 'P-404'" in result
    assert not openings.called


async def test_position_costs_accepts_position_names(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    positions = mock_api.post(POSITION_SEARCH).mock(
        side_effect=_by_filter(
            **{
                "/position/name=P-7": [_position(7, "P-7", 70)],
                "/position/id=7": [_position(7, "P-7", 70)],
            }
        )
    )
    mock_api.post(BUDGET_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {
                        "/positionBudget/id": {"value": 70},
                        "/positionBudget/currency": {"value": "EUR"},
                    }
                ]
            },
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server, "hibob_get_position_costs", {"position_ids": ["P-7"]}
        )
    )

    assert result["count"] == 1
    assert result["entries"][0]["values"]["/position/name"] == "P-7"
    assert result["resolved_positions"] == {"P-7": "7"}
    assert json.loads(positions.calls[1].request.content)["filters"] == [
        {"fieldId": "/position/id", "operator": "equals", "values": ["7"]}
    ]
