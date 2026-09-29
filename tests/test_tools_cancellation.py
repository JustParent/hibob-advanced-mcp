"""Scheduling a position's cancellation for a later date.

HiBob keeps the position "Cancelled soon" until midnight on the date, then
cancels it and unassigns whoever holds it. So a held position is only
scheduled when the caller says to unassign the holder, and nothing is sent
for a position already cancelled or a date that is not a real future day.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from mcp.server.fastmcp import FastMCP

from conftest import call_tool

TOOL = "hibob_schedule_position_cancellation"
POSITION_SEARCH = "/objects/position/search"
SCHEDULE = "/workforce-planning/positions/schedule-cancellation"
LATER = "2099-03-31"


def _position(status: str | None, holder: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "/position/id": {"value": 77, "humanReadable": "77"},
        "/position/name": {"value": "P-77", "humanReadable": "P-77"},
    }
    if status is not None:
        row["/position/status"] = {"value": status, "humanReadable": status}
    if holder is not None:
        row["/position/filledBy"] = {
            "value": "1000000000000000001",
            "humanReadable": holder,
        }
    return row


def _searches(mock_api: respx.MockRouter, *rows: dict[str, Any]) -> respx.Route:
    """Answer successive position searches with one row each."""
    return mock_api.post(POSITION_SEARCH).mock(
        side_effect=[httpx.Response(200, json=[row]) for row in rows]
    )


async def test_schedule_sends_the_position_as_a_number_with_the_date(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    _searches(mock_api, _position("vacant"), _position("cancelledSoon"))
    route = mock_api.post(SCHEDULE).mock(return_value=httpx.Response(200, json={}))

    result = json.loads(
        await call_tool(
            mcp_server, TOOL, {"position_id": "P-77", "cancellation_date": LATER}
        )
    )

    assert json.loads(route.calls.last.request.content) == {
        "positionIds": [77],
        "cancellationDate": LATER,
    }
    assert result["position_id"] == "77"
    assert result["cancellation_date"] == LATER
    assert result["verified"] is True


async def test_schedule_looks_up_the_holder_with_the_start_date(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """HiBob leaves the holder out of some rows unless the start date is
    asked for too, which would let a held position pass as vacant."""
    searches = _searches(mock_api, _position("vacant"), _position("cancelledSoon"))
    mock_api.post(SCHEDULE).mock(return_value=httpx.Response(200, json={}))

    await call_tool(mcp_server, TOOL, {"position_id": "77", "cancellation_date": LATER})

    fields = json.loads(searches.calls[0].request.content)["fields"]
    assert "/position/filledBy" in fields
    assert "/position/actualStartDate" in fields


@pytest.mark.parametrize("status", ["filled", "starting", "departing"])
async def test_schedule_refuses_a_held_position_unless_told_to_unassign(
    mcp_server: FastMCP, mock_api: respx.MockRouter, status: str
) -> None:
    _searches(mock_api, _position(status, holder="Jane Doe"))
    route = mock_api.post(SCHEDULE)

    result = await call_tool(
        mcp_server, TOOL, {"position_id": "77", "cancellation_date": LATER}
    )

    assert result.startswith("Error:")
    assert "Jane Doe" in result
    assert "unassign_holder" in result
    assert not route.called


async def test_schedule_goes_ahead_for_a_held_position_when_told_to_unassign(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    _searches(
        mock_api, _position("filled", holder="Jane Doe"), _position("cancelledSoon")
    )
    route = mock_api.post(SCHEDULE).mock(return_value=httpx.Response(200, json={}))

    result = json.loads(
        await call_tool(
            mcp_server,
            TOOL,
            {"position_id": "77", "cancellation_date": LATER, "unassign_holder": True},
        )
    )

    assert route.called
    assert result["unassigns"] == "Jane Doe"
    assert result["verified"] is True


@pytest.mark.parametrize("status", ["cancelled", "cancelledSoon"])
async def test_schedule_refuses_a_position_already_cancelled(
    mcp_server: FastMCP, mock_api: respx.MockRouter, status: str
) -> None:
    _searches(mock_api, _position(status))
    route = mock_api.post(SCHEDULE)

    result = await call_tool(
        mcp_server, TOOL, {"position_id": "77", "cancellation_date": LATER}
    )

    assert result.startswith("Error:")
    assert not route.called


@pytest.mark.parametrize("given", ["31/03/2099", "2099-02-30", "2020-01-01", ""])
async def test_schedule_refuses_a_date_that_is_not_a_future_day_before_any_request(
    mcp_server: FastMCP, mock_api: respx.MockRouter, given: str
) -> None:
    route = mock_api.route()

    result = await call_tool(
        mcp_server, TOOL, {"position_id": "77", "cancellation_date": given}
    )

    assert result.startswith("Error:")
    assert not route.called


async def test_schedule_points_a_past_date_at_cancelling_now(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    result = await call_tool(
        mcp_server, TOOL, {"position_id": "77", "cancellation_date": "2020-01-01"}
    )

    assert "hibob_cancel_position" in result


async def test_schedule_flags_a_position_that_does_not_read_back_cancelled(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    _searches(mock_api, _position("vacant"), _position("vacant"))
    mock_api.post(SCHEDULE).mock(return_value=httpx.Response(200, json={}))

    result = json.loads(
        await call_tool(
            mcp_server, TOOL, {"position_id": "77", "cancellation_date": LATER}
        )
    )

    assert result["verified"] is False
    assert "vacant" in result["verification_error"]


async def test_schedule_reports_the_budget_end_date_read_back(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    """Whether scheduling sets the budget end date is not documented, so the
    read-back shows it."""
    searches = _searches(mock_api, _position("vacant"), _position("cancelledSoon"))
    mock_api.post(SCHEDULE).mock(return_value=httpx.Response(200, json={}))

    await call_tool(mcp_server, TOOL, {"position_id": "77", "cancellation_date": LATER})

    assert (
        "/position/endEffectiveDate"
        in json.loads(searches.calls[1].request.content)["fields"]
    )


# ------------------------------------------ creating a position that ends

CREATE = "/workforce-planning/positions"
OPENING_SEARCH = "/positions/position-openings/search"
NEW_POSITION = {
    "/position/effectiveDate": "2099-01-01",
    "/position/fte": 100,
    "/position/department": "263717557",
    "/position/site": 2555828,
    "/position/jobProfile": 31051810,
}
NEW_OPENING = {"/positionOpening/expectedStartDate": "2099-02-01"}
ENDS = "2099-12-31"


def _new_position(status: str) -> dict[str, Any]:
    """The created position as HiBob reads it back, holding what was written."""
    row = _position(status)
    row["/position/id"] = {"value": 1, "humanReadable": "1"}
    row.update({field: {"value": value} for field, value in NEW_POSITION.items()})
    return row


def _mock_create(mock_api: respx.MockRouter, status: str = "cancelledSoon") -> None:
    mock_api.post(CREATE).mock(
        return_value=httpx.Response(
            200, json=[{"positionId": 1, "positionOpeningId": 2}]
        )
    )
    mock_api.post(POSITION_SEARCH).mock(
        return_value=httpx.Response(200, json=[_new_position(status)])
    )
    mock_api.post(OPENING_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {
                        "/positionOpening/id": {"value": 2},
                        "/positionOpening/positionId": {"value": 1},
                        "/positionOpening/expectedStartDate": {"value": "2099-02-01"},
                    }
                ]
            },
        )
    )


def _create(**extra: Any) -> dict[str, Any]:
    return {"position_fields": NEW_POSITION, "opening_fields": NEW_OPENING, **extra}


async def test_create_with_a_cancellation_date_schedules_it_after_the_create(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    _mock_create(mock_api)
    schedule = mock_api.post(SCHEDULE).mock(return_value=httpx.Response(200, json={}))

    result = json.loads(
        await call_tool(
            mcp_server, "hibob_create_position", _create(cancellation_date=ENDS)
        )
    )

    assert json.loads(schedule.calls.last.request.content) == {
        "positionIds": [1],
        "cancellationDate": ENDS,
    }
    paths = [call.request.url.path for call in mock_api.calls]
    assert paths.index(f"/v1{CREATE}") < paths.index(f"/v1{SCHEDULE}")
    assert result["positionId"] == 1
    assert result["cancellation_date"] == ENDS
    assert result["verified"] is True


async def test_create_reads_back_the_status_and_budget_end_date_it_scheduled(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    _mock_create(mock_api, status="vacant")
    mock_api.post(SCHEDULE).mock(return_value=httpx.Response(200, json={}))

    result = json.loads(
        await call_tool(
            mcp_server, "hibob_create_position", _create(cancellation_date=ENDS)
        )
    )

    read_back = [
        json.loads(call.request.content)
        for call in mock_api.calls
        if call.request.url.path == f"/v1{POSITION_SEARCH}"
    ]
    assert "/position/endEffectiveDate" in read_back[-1]["fields"]
    assert result["verified"] is False
    assert "vacant" in result["verification_error"]


@pytest.mark.parametrize(
    "given",
    [
        "2099-01-01",  # the budget date itself
        "2099-01-15",  # before the opening's expected start
        "2099-02-01",  # the expected start itself
        "2020-01-01",
        "31/12/2099",
    ],
)
async def test_create_refuses_a_cancellation_date_before_the_role_starts(
    mcp_server: FastMCP, mock_api: respx.MockRouter, given: str
) -> None:
    route = mock_api.route()

    result = await call_tool(
        mcp_server, "hibob_create_position", _create(cancellation_date=given)
    )

    assert result.startswith("Error:")
    assert not route.called


async def test_create_reports_a_cancellation_hibob_refused_without_hiding_the_ids(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    _mock_create(mock_api, status="vacant")
    mock_api.post(SCHEDULE).mock(
        return_value=httpx.Response(
            400, json={"key": "invalid.request", "error": "Date not allowed"}
        )
    )

    result = json.loads(
        await call_tool(
            mcp_server, "hibob_create_position", _create(cancellation_date=ENDS)
        )
    )

    assert result["positionId"] == 1
    assert result["verified"] is False
    assert "cancellation_date" not in result
    assert "Date not allowed" in result["verification_error"]
    assert "hibob_schedule_position_cancellation" in result["verification_error"]


async def test_create_refuses_the_budget_end_date_and_points_at_cancellation_date(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    route = mock_api.route()

    result = await call_tool(
        mcp_server,
        "hibob_create_position",
        {
            "position_fields": {**NEW_POSITION, "/position/endEffectiveDate": ENDS},
            "opening_fields": NEW_OPENING,
        },
    )

    assert result.startswith("Error:")
    assert "cancellation_date" in result
    assert not route.called


async def test_create_without_a_cancellation_date_schedules_nothing(
    mcp_server: FastMCP, mock_api: respx.MockRouter
) -> None:
    _mock_create(mock_api, status="vacant")
    schedule = mock_api.post(SCHEDULE)

    result = json.loads(await call_tool(mcp_server, "hibob_create_position", _create()))

    assert not schedule.called
    assert result["verified"] is True
