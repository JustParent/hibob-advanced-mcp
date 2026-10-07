"""hibob_terminate_employee: one checked termination, sent once."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError

from conftest import call_tool

EMPLOYEE_ID = "3332883884017713238"
JANE = {"id": EMPLOYEE_ID, "displayName": "Jane Smith", "email": "jane@x.com"}
TERMINATE = f"/employees/{EMPLOYEE_ID}/terminate"
REASONS = {
    "name": "terminationReason",
    "values": [
        {"id": "Redundant", "name": "Redundant", "value": "Redundant"},
        {"id": "283510", "name": "Resigned", "value": "Resigned"},
    ],
}
REASON_TYPES = {
    "name": "lifecycleReasonType",
    "values": [{"id": "283600", "name": "End of Contract", "value": "End of Contract"}],
}


def _people(mock_api: respx.MockRouter, *employees: dict) -> respx.Route:
    return mock_api.post("/people/search").mock(
        return_value=httpx.Response(200, json={"employees": list(employees)})
    )


def _terminate(mock_api: respx.MockRouter, status: int = 200, **kw) -> respx.Route:
    return mock_api.post(TERMINATE).mock(return_value=httpx.Response(status, **kw))


async def test_terminates_by_id_once_and_names_the_employee(
    mock_api, mcp_server
) -> None:
    lookup = _people(mock_api, JANE)
    route = _terminate(mock_api)
    text = await call_tool(
        mcp_server,
        "hibob_terminate_employee",
        {"employee": EMPLOYEE_ID, "termination_date": "2026-10-31"},
    )
    query = json.loads(lookup.calls.last.request.content)
    assert query["filters"] == [
        {"fieldPath": "root.id", "operator": "equals", "values": [EMPLOYEE_ID]}
    ]
    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == {
        "terminationDate": "2026-10-31"
    }
    result = json.loads(text)
    assert result["status"] == "termination_added"
    assert result["employee"] == {
        "id": EMPLOYEE_ID,
        "name": "Jane Smith",
        "email": "jane@x.com",
    }
    assert result["termination"] == {"terminationDate": "2026-10-31"}


async def test_terminates_by_work_email(mock_api, mcp_server) -> None:
    lookup = _people(mock_api, JANE)
    route = _terminate(mock_api)
    text = await call_tool(
        mcp_server,
        "hibob_terminate_employee",
        {"employee": " Jane@X.com ", "termination_date": "2026-10-31"},
    )
    query = json.loads(lookup.calls.last.request.content)
    assert query["filters"][0]["fieldPath"] == "root.email"
    assert query["filters"][0]["values"] == ["jane@x.com"]
    assert route.call_count == 1
    assert json.loads(text)["employee"]["id"] == EMPLOYEE_ID


@pytest.mark.parametrize(
    ("employee", "found", "expected"),
    [
        (EMPLOYEE_ID, [], "No active employee"),
        ("nobody@x.com", [], "No active employee"),
        (
            "shared@x.com",
            [JANE, {"id": "99", "displayName": "John Smith", "email": "shared@x.com"}],
            "John Smith",
        ),
    ],
)
async def test_unknown_or_ambiguous_employee_sends_nothing(
    mock_api, mcp_server, employee, found, expected
) -> None:
    _people(mock_api, *found)
    route = _terminate(mock_api)
    text = await call_tool(
        mcp_server,
        "hibob_terminate_employee",
        {"employee": employee, "termination_date": "2026-10-31"},
    )
    assert text.startswith("Error:")
    assert expected in text
    assert "Nothing was written" in text
    assert not route.called


async def test_sends_every_optional_field(mock_api, mcp_server) -> None:
    _people(mock_api, JANE)
    mock_api.get("/company/named-lists/terminationReason").mock(
        return_value=httpx.Response(200, json=REASONS)
    )
    mock_api.get("/company/named-lists/lifecycleReasonType").mock(
        return_value=httpx.Response(200, json=REASON_TYPES)
    )
    route = _terminate(mock_api)
    text = await call_tool(
        mcp_server,
        "hibob_terminate_employee",
        {
            "employee": EMPLOYEE_ID,
            "termination_date": "2026-10-31",
            "last_day_of_work": "2026-10-30",
            "termination_reason": "resigned",
            "reason_type": "End of contract",
            "notice_period_length": 30,
            "notice_period_unit": "days",
        },
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "terminationDate": "2026-10-31",
        "lastDayOfWork": "2026-10-30",
        "terminationReason": "283510",
        "reasonType": "283600",
        "noticePeriod": {"unit": "days", "length": 30},
    }
    assert json.loads(text)["termination"] == sent


async def test_unmatched_reason_offers_candidates_and_sends_nothing(
    mock_api, mcp_server
) -> None:
    _people(mock_api, JANE)
    mock_api.get("/company/named-lists/terminationReason").mock(
        return_value=httpx.Response(200, json=REASONS)
    )
    route = _terminate(mock_api)
    text = await call_tool(
        mcp_server,
        "hibob_terminate_employee",
        {
            "employee": EMPLOYEE_ID,
            "termination_date": "2026-10-31",
            "termination_reason": "Redundancy",
        },
    )
    assert text.startswith("Error:")
    assert "Redundancy" in text
    assert "Redundant" in text
    assert not route.called


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"termination_date": "31/10/2026"}, "termination_date"),
        ({"termination_date": "2026-02-30"}, "termination_date"),
        (
            {"termination_date": "2026-10-31", "last_day_of_work": "2026-11-01"},
            "after the termination date",
        ),
        (
            {"termination_date": "2026-10-31", "notice_period_length": 4},
            "notice_period_unit",
        ),
        (
            {"termination_date": "2026-10-31", "notice_period_unit": "weeks"},
            "notice_period_length",
        ),
        (
            {
                "termination_date": "2026-10-31",
                "notice_period_length": -1,
                "notice_period_unit": "weeks",
            },
            "notice_period_length",
        ),
    ],
)
async def test_invalid_arguments_send_nothing(
    mock_api, mcp_server, arguments, expected
) -> None:
    route = mock_api.route().mock(return_value=httpx.Response(200, json={}))
    text = await call_tool(
        mcp_server, "hibob_terminate_employee", {"employee": EMPLOYEE_ID, **arguments}
    )
    assert text.startswith("Error:")
    assert expected in text
    assert not route.called


async def test_server_error_is_not_retried(mock_api, mcp_server) -> None:
    _people(mock_api, JANE)
    route = _terminate(mock_api, 503)
    text = await call_tool(
        mcp_server,
        "hibob_terminate_employee",
        {"employee": EMPLOYEE_ID, "termination_date": "2026-10-31"},
    )
    assert text.startswith("Error:")
    assert route.call_count == 1


async def test_403_names_the_lifecycle_permission(mock_api, mcp_server) -> None:
    _people(mock_api, JANE)
    _terminate(mock_api, 403, json={})
    text = await call_tool(
        mcp_server,
        "hibob_terminate_employee",
        {"employee": EMPLOYEE_ID, "termination_date": "2026-10-31"},
    )
    assert "Lifecycle" in text
    assert "Manage positions" not in text


async def test_400_points_at_dates_and_reason_lists(mock_api, mcp_server) -> None:
    _people(mock_api, JANE)
    _terminate(mock_api, 400, json={"key": "bad", "error": "Invalid reason"})
    text = await call_tool(
        mcp_server,
        "hibob_terminate_employee",
        {"employee": EMPLOYEE_ID, "termination_date": "2026-10-31"},
    )
    assert "Invalid reason" in text
    assert "terminationReason" in text
    assert "hibob_list_workforce_fields" not in text


async def test_terminate_absent_in_read_only(server_factory) -> None:
    with pytest.raises(ToolError, match="Unknown tool"):
        await call_tool(
            server_factory(read_only=True),
            "hibob_terminate_employee",
            {"employee": EMPLOYEE_ID, "termination_date": "2026-10-31"},
        )


async def test_denied_reason_list_does_not_blame_positions(
    mock_api, mcp_server
) -> None:
    _people(mock_api, JANE)
    mock_api.get("/company/named-lists/terminationReason").mock(
        return_value=httpx.Response(403, json={})
    )
    route = _terminate(mock_api)
    text = await call_tool(
        mcp_server,
        "hibob_terminate_employee",
        {
            "employee": EMPLOYEE_ID,
            "termination_date": "2026-10-31",
            "termination_reason": "Resigned",
        },
    )
    assert text.startswith("Error:")
    assert "terminationReason list" in text
    assert "Manage positions" not in text
    assert not route.called
