"""Time off tools: policy types, who's out, requests, balances, booking and
cancelling.

Payloads follow what HiBob returned live (tests/fixtures/timeoff/observations.md).
The cancel tool matters most: HiBob's DELETE ignores the employee in its path,
so the request is read under the employee first and nothing is sent unless it
is theirs, still live and, without cancel_started, not yet begun.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from conftest import call_tool
from people_data import EMPLOYEE_ID, MANAGER_ID, FakePeople

REQUEST_ID = "64280382"
REQUEST_PATH = f"/timeoff/employees/{EMPLOYEE_ID}/requests/{REQUEST_ID}"
WHOS_OUT = "/timeoff/whosout"


def _request(status: str, start: str = "2026-11-16", end: str | None = None) -> dict:
    return {
        "requestId": int(REQUEST_ID),
        "employeeId": EMPLOYEE_ID,
        "policyTypeDisplayName": "Holiday",
        "approved": status == "approved",
        "description": "Half term",
        "durationUnit": "days",
        "totalDuration": 1,
        "status": status,
        "startDate": start,
        "startPortion": "all_day",
        "endDate": end or start,
        "endPortion": "all_day",
        "type": "days",
    }


def _out(employee_id: str, request_id: int, status: str = "approved") -> dict:
    return {
        "employeeId": employee_id,
        "policyType": "Holiday",
        "policyTypeDisplayName": "Holiday",
        "requestId": request_id,
        "status": status,
        "employeeDisplayName": "Someone",
        "visibility": "Public",
        "startDate": "2026-11-16",
        "startPortion": "all_day",
        "endDate": "2026-11-16",
        "endPortion": "all_day",
        "type": "days",
    }


def _reads(mock_api: respx.MockRouter, *statuses: str, **dates: Any) -> respx.Route:
    """Answer successive reads of the request with these statuses."""
    return mock_api.get(REQUEST_PATH).mock(
        side_effect=[httpx.Response(200, json=_request(s, **dates)) for s in statuses]
    )


async def _call(mcp: FastMCP, name: str, **arguments: Any) -> str:
    return await call_tool(mcp, name, arguments)


async def _json(mcp: FastMCP, name: str, **arguments: Any) -> dict:
    text = await _call(mcp, name, **arguments)
    assert not text.startswith("Error:"), text
    return json.loads(text)


# Policy types


async def test_policy_types_are_listed(mock_api, mcp_server) -> None:
    mock_api.get("/timeoff/policy-types").mock(
        return_value=httpx.Response(200, json={"policyTypes": ["Holiday", "Sick"]})
    )
    result = await _json(mcp_server, "hibob_list_timeoff_policy_types")
    assert result == {"policy_types": ["Holiday", "Sick"]}


async def test_a_policy_type_is_described_with_its_policies_and_reason_codes(
    mock_api, mcp_server
) -> None:
    mock_api.get("/timeoff/policy-types/PTO%20%28USA%29").mock(
        return_value=httpx.Response(
            200, json={"name": "PTO (USA)", "activity": "paidTimeoff", "unit": "days"}
        )
    )
    names = mock_api.get(
        "/timeoff/policies/names", params={"policyTypeName": "PTO (USA)"}
    ).mock(return_value=httpx.Response(200, json={"policies": ["PTO policy"]}))
    policy = mock_api.get(
        "/timeoff/policies", params={"policyName": "PTO policy"}
    ).mock(
        return_value=httpx.Response(
            200, json={"name": "PTO policy", "approvalRequired": True, "unit": "days"}
        )
    )
    mock_api.get("/timeoff/policy-types/PTO%20%28USA%29/reason-codes").mock(
        return_value=httpx.Response(
            200, json={"reasonCodes": [{"id": 7, "name": "Cold"}]}
        )
    )
    result = await _json(
        mcp_server, "hibob_list_timeoff_policy_types", policy_type=" PTO (USA) "
    )
    assert names.called and policy.called
    assert result["policy_type"]["unit"] == "days"
    assert result["policies"] == [
        {"name": "PTO policy", "approvalRequired": True, "unit": "days"}
    ]
    assert result["reason_codes"] == [{"id": 7, "name": "Cold"}]


async def test_an_unknown_policy_type_gives_hibobs_reason(mock_api, mcp_server) -> None:
    mock_api.get("/timeoff/policy-types/Nonsense").mock(
        return_value=httpx.Response(
            400,
            json={
                "key": "loa.policy.policy_type_does_not_exits",
                "error": "Policy type doesn't exist",
                "args": [],
            },
        )
    )
    text = await _call(
        mcp_server, "hibob_list_timeoff_policy_types", policy_type="Nonsense"
    )
    assert text.startswith("Error: HiBob refused the time off request (400)")
    assert "Policy type doesn't exist" in text


# Who's out


async def test_requests_are_listed_with_pending_and_private_by_default(
    mock_api, mcp_server
) -> None:
    route = mock_api.get(WHOS_OUT).mock(
        return_value=httpx.Response(200, json={"outs": [_out(EMPLOYEE_ID, 1)]})
    )
    result = await _json(
        mcp_server,
        "hibob_list_timeoff_requests",
        from_date="2026-11-01",
        to_date="2026-11-30",
    )
    params = route.calls.last.request.url.params
    assert dict(params) == {
        "from": "2026-11-01",
        "to": "2026-11-30",
        "includeHourly": "true",
        "includePending": "true",
        "includePrivate": "true",
    }
    assert result["count"] == 1
    assert result["requests"][0]["requestId"] == 1
    assert "cancelled and declined" in result["note"]


async def test_requests_can_be_narrowed_to_one_employee(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    mock_api.get(WHOS_OUT).mock(
        return_value=httpx.Response(
            200,
            json={
                "outs": [
                    _out(EMPLOYEE_ID, 1),
                    _out(MANAGER_ID, 2),
                    _out(EMPLOYEE_ID, 3, "pending"),
                ]
            },
        )
    )
    result = await _json(
        mcp_server,
        "hibob_list_timeoff_requests",
        from_date="2026-11-01",
        to_date="2026-11-30",
        employee="jane@x.com",
        include_private=False,
    )
    assert result["employee"]["id"] == EMPLOYEE_ID
    assert [r["requestId"] for r in result["requests"]] == [1, 3]


async def test_a_backwards_range_sends_nothing(mock_api, mcp_server) -> None:
    route = mock_api.route().mock(return_value=httpx.Response(200, json={}))
    text = await _call(
        mcp_server,
        "hibob_list_timeoff_requests",
        from_date="2026-12-01",
        to_date="2026-11-01",
    )
    assert text.startswith("Error:")
    assert not route.called


async def test_a_whos_out_403_names_the_whos_out_permission(
    mock_api, mcp_server
) -> None:
    mock_api.get(WHOS_OUT).mock(return_value=httpx.Response(403, json={}))
    text = await _call(
        mcp_server,
        "hibob_list_timeoff_requests",
        from_date="2026-11-01",
        to_date="2026-11-30",
    )
    assert "See who's out" in text
    assert "Lifecycle" not in text


# One request, balances and changes


async def test_a_request_is_read_under_its_employee(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    _reads(mock_api, "pending_approval")
    result = await _json(
        mcp_server,
        "hibob_get_timeoff_request",
        employee=EMPLOYEE_ID,
        request_id=REQUEST_ID,
    )
    assert result["employee"]["name"] == "Jane Smith"
    assert result["request"]["status"] == "pending_approval"


async def test_someone_elses_request_is_not_found(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    mock_api.get(REQUEST_PATH).mock(return_value=httpx.Response(404))
    text = await _call(
        mcp_server,
        "hibob_get_timeoff_request",
        employee=EMPLOYEE_ID,
        request_id=REQUEST_ID,
    )
    assert text.startswith(f"Error: Jane Smith has no time off request {REQUEST_ID}")
    assert "another employee's request" in text


async def test_a_balance_is_read_for_today_unless_a_day_is_given(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    route = mock_api.get(f"/timeoff/employees/{EMPLOYEE_ID}/balance").mock(
        return_value=httpx.Response(200, json={"totalBalanceAsOfDate": 12.5})
    )
    result = await _json(
        mcp_server,
        "hibob_get_timeoff_balance",
        employee="jane@x.com",
        policy_type="Holiday",
    )
    assert dict(route.calls.last.request.url.params) == {
        "policyType": "Holiday",
        "date": "2026-10-13",
    }
    assert result["balance"] == {"totalBalanceAsOfDate": 12.5}
    await _json(
        mcp_server,
        "hibob_get_timeoff_balance",
        employee="jane@x.com",
        policy_type="Holiday",
        as_of="2026-12-31",
    )
    assert route.calls.last.request.url.params["date"] == "2026-12-31"


async def test_a_balance_403_names_the_balance_permission(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    mock_api.get(f"/timeoff/employees/{EMPLOYEE_ID}/balance").mock(
        return_value=httpx.Response(403, json={})
    )
    text = await _call(
        mcp_server,
        "hibob_get_timeoff_balance",
        employee=EMPLOYEE_ID,
        policy_type="Holiday",
    )
    assert "See people's time off and sick leave balances" in text


async def test_changes_take_a_date_as_midnight_utc_and_filter_by_employee(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    route = mock_api.get("/timeoff/requests/changes").mock(
        return_value=httpx.Response(
            200,
            json={
                "changes": [
                    {
                        "changeType": "Canceled",
                        "employeeId": EMPLOYEE_ID,
                        "requestId": 1,
                    },
                    {"changeType": "Created", "employeeId": MANAGER_ID, "requestId": 2},
                ]
            },
        )
    )
    result = await _json(
        mcp_server,
        "hibob_get_timeoff_changes",
        since="2026-10-01",
        employee="Jane Smith",
    )
    assert route.calls.last.request.url.params["since"] == "2026-10-01T00:00:00.000Z"
    assert result["count"] == 1
    assert result["changes"][0]["changeType"] == "Canceled"


async def test_changes_beyond_six_months_give_hibobs_reason(
    mock_api, mcp_server
) -> None:
    mock_api.get("/timeoff/requests/changes").mock(
        return_value=httpx.Response(
            400,
            json={
                "key": "exception.changes.api.date.range.too.large",
                "error": "date range should be up to six months",
                "args": [],
            },
        )
    )
    text = await _call(mcp_server, "hibob_get_timeoff_changes", since="2026-01-01")
    assert "date range should be up to six months" in text


# Booking


async def test_a_single_day_is_booked_and_read_back(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    post = mock_api.post(f"/timeoff/employees/{EMPLOYEE_ID}/requests").mock(
        return_value=httpx.Response(200, json={"id": int(REQUEST_ID)})
    )
    _reads(mock_api, "pending_approval")
    result = await _json(
        mcp_server,
        "hibob_submit_timeoff_request",
        employee="jane@x.com",
        policy_type="Holiday",
        start_date="2026-11-16",
        description="Half term",
    )
    assert post.call_count == 1
    assert json.loads(post.calls.last.request.content) == {
        "policyType": "Holiday",
        "requestRangeType": "days",
        "startDate": "2026-11-16",
        "endDate": "2026-11-16",
        "startDatePortion": "all_day",
        "endDatePortion": "all_day",
        "skipManagerApproval": False,
        "description": "Half term",
    }
    assert result["request_id"] == REQUEST_ID
    assert result["status"] == "pending_approval"


async def test_half_days_reason_codes_and_approval_are_sent(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    post = mock_api.post(f"/timeoff/employees/{EMPLOYEE_ID}/requests").mock(
        return_value=httpx.Response(200, json={"id": int(REQUEST_ID)})
    )
    _reads(mock_api, "approved")
    await _json(
        mcp_server,
        "hibob_submit_timeoff_request",
        employee=EMPLOYEE_ID,
        policy_type="Sick",
        start_date="2026-11-16",
        end_date="2026-11-18",
        start_portion="afternoon",
        end_portion="morning",
        reason_code=7,
        skip_manager_approval=True,
    )
    body = json.loads(post.calls.last.request.content)
    assert body["startDatePortion"] == "afternoon"
    assert body["endDatePortion"] == "morning"
    assert body["reasonCode"] == 7
    assert body["skipManagerApproval"] is True


async def test_an_end_before_the_start_sends_nothing(mock_api, mcp_server) -> None:
    route = mock_api.route().mock(return_value=httpx.Response(200, json={}))
    text = await _call(
        mcp_server,
        "hibob_submit_timeoff_request",
        employee=EMPLOYEE_ID,
        policy_type="Holiday",
        start_date="2026-11-18",
        end_date="2026-11-16",
    )
    assert text.startswith("Error: end_date 2026-11-16 is before start_date")
    assert not route.called


async def test_a_refused_booking_names_the_conflict(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    mock_api.post(f"/timeoff/employees/{EMPLOYEE_ID}/requests").mock(
        return_value=httpx.Response(
            400,
            json={
                "key": "timeoff_reject_reason.conflicted.not.allow.override",
                "error": "Conflicts with request/s which cannot be overridden:",
                "args": ["Pending approval Holiday request from 23/11/2026 (morning)"],
            },
        )
    )
    text = await _call(
        mcp_server,
        "hibob_submit_timeoff_request",
        employee=EMPLOYEE_ID,
        policy_type="Holiday",
        start_date="2026-11-23",
    )
    assert (
        "Conflicts with request/s which cannot be overridden: Pending approval "
        "Holiday request from 23/11/2026 (morning)" in text
    )


async def test_a_booking_that_cannot_be_read_back_still_reports_its_id(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    mock_api.post(f"/timeoff/employees/{EMPLOYEE_ID}/requests").mock(
        return_value=httpx.Response(200, json={"id": int(REQUEST_ID)})
    )
    mock_api.get(REQUEST_PATH).mock(return_value=httpx.Response(403, json={}))
    result = await _json(
        mcp_server,
        "hibob_submit_timeoff_request",
        employee=EMPLOYEE_ID,
        policy_type="Holiday",
        start_date="2026-11-16",
    )
    assert result["request_id"] == REQUEST_ID
    assert "Create, edit and cancel" in result["readback_error"]


async def test_args_that_repeat_the_error_are_not_added_again(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    mock_api.post(f"/timeoff/employees/{EMPLOYEE_ID}/requests").mock(
        return_value=httpx.Response(
            400,
            json={
                "key": "timeoff_reject_reason.zeroDurationRequestOnNonWorkingDay.onBehalf",
                "error": "Employee isn\u2019t working on Saturday, Sunday.",
                "args": ["Saturday, Sunday"],
            },
        )
    )
    text = await _call(
        mcp_server,
        "hibob_submit_timeoff_request",
        employee=EMPLOYEE_ID,
        policy_type="Holiday",
        start_date="2026-11-28",
        end_date="2026-11-29",
    )
    assert text.count("Saturday, Sunday") == 1


# Cancelling


@pytest.mark.parametrize("status", ["pending_approval", "approved"])
async def test_a_live_request_is_cancelled_once_and_read_back(
    mock_api, mcp_server, status: str
) -> None:
    FakePeople(mock_api)
    reads = _reads(mock_api, status, "canceled")
    delete = mock_api.delete(REQUEST_PATH).mock(return_value=httpx.Response(200))
    result = await _json(
        mcp_server,
        "hibob_cancel_timeoff_request",
        employee="jane@x.com",
        request_id=REQUEST_ID,
    )
    assert reads.call_count == 2
    assert delete.call_count == 1
    assert result["cancelled"] is True
    assert result["previous_status"] == status
    assert result["status"] == "canceled"
    assert result["request"]["startDate"] == "2026-11-16"


async def test_someone_elses_request_is_never_cancelled(mock_api, mcp_server) -> None:
    """HiBob's DELETE would cancel it: only the read refuses it."""
    FakePeople(mock_api)
    mock_api.get(REQUEST_PATH).mock(return_value=httpx.Response(404))
    delete = mock_api.delete(REQUEST_PATH).mock(return_value=httpx.Response(200))
    text = await _call(
        mcp_server,
        "hibob_cancel_timeoff_request",
        employee=EMPLOYEE_ID,
        request_id=REQUEST_ID,
    )
    assert text.startswith(f"Error: Jane Smith has no time off request {REQUEST_ID}")
    assert not delete.called


async def test_an_already_cancelled_request_sends_nothing(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    _reads(mock_api, "canceled")
    delete = mock_api.delete(REQUEST_PATH).mock(return_value=httpx.Response(200))
    result = await _json(
        mcp_server,
        "hibob_cancel_timeoff_request",
        employee=EMPLOYEE_ID,
        request_id=REQUEST_ID,
    )
    assert not delete.called
    assert result["cancelled"] is False
    assert "already cancelled" in result["note"]


async def test_any_other_status_is_refused(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    _reads(mock_api, "declined")
    delete = mock_api.delete(REQUEST_PATH).mock(return_value=httpx.Response(200))
    text = await _call(
        mcp_server,
        "hibob_cancel_timeoff_request",
        employee=EMPLOYEE_ID,
        request_id=REQUEST_ID,
    )
    assert "'declined'" in text
    assert "Nothing was written." in text
    assert not delete.called


@pytest.mark.parametrize(
    ("start", "end"),
    [("2026-10-05", "2026-10-05"), ("2026-10-12", "2026-10-14")],
    ids=["past", "in progress"],
)
async def test_a_started_request_needs_cancel_started(
    mock_api, mcp_server, start: str, end: str
) -> None:
    FakePeople(mock_api)
    _reads(mock_api, "approved", "approved", "canceled", start=start, end=end)
    delete = mock_api.delete(REQUEST_PATH).mock(return_value=httpx.Response(200))
    text = await _call(
        mcp_server,
        "hibob_cancel_timeoff_request",
        employee=EMPLOYEE_ID,
        request_id=REQUEST_ID,
    )
    assert "started before today" in text
    assert "days already passed" in text
    assert not delete.called

    result = await _json(
        mcp_server,
        "hibob_cancel_timeoff_request",
        employee=EMPLOYEE_ID,
        request_id=REQUEST_ID,
        cancel_started=True,
    )
    assert delete.call_count == 1
    assert result["cancelled"] is True


async def test_a_request_starting_today_is_cancelled(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    _reads(mock_api, "approved", "canceled", start="2026-10-13")
    delete = mock_api.delete(REQUEST_PATH).mock(return_value=httpx.Response(200))
    result = await _json(
        mcp_server,
        "hibob_cancel_timeoff_request",
        employee=EMPLOYEE_ID,
        request_id=REQUEST_ID,
    )
    assert delete.call_count == 1
    assert result["cancelled"] is True


async def test_a_request_still_live_after_cancelling_is_flagged(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    _reads(mock_api, "approved", "approved")
    mock_api.delete(REQUEST_PATH).mock(return_value=httpx.Response(200))
    result = await _json(
        mcp_server,
        "hibob_cancel_timeoff_request",
        employee=EMPLOYEE_ID,
        request_id=REQUEST_ID,
    )
    assert result["cancelled"] is False
    assert "still shows the request as 'approved'" in result["warning"]


async def test_a_refused_cancellation_names_the_requests_permission(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    _reads(mock_api, "approved")
    mock_api.delete(REQUEST_PATH).mock(return_value=httpx.Response(403, json={}))
    text = await _call(
        mcp_server,
        "hibob_cancel_timeoff_request",
        employee=EMPLOYEE_ID,
        request_id=REQUEST_ID,
    )
    assert "Create, edit and cancel people's requests" in text
    assert "approved request" in text


@pytest.mark.parametrize("request_id", ["", "abc", "12-3"])
async def test_a_request_id_must_be_numeric(mock_api, mcp_server, request_id) -> None:
    route = mock_api.route().mock(return_value=httpx.Response(200, json={}))
    for tool in ("hibob_cancel_timeoff_request", "hibob_get_timeoff_request"):
        text = await _call(
            mcp_server, tool, employee=EMPLOYEE_ID, request_id=request_id
        )
        assert text.startswith("Error: request_id must be HiBob's numeric request ID")
    assert not route.called


# Gating


async def test_booking_and_cancelling_are_absent_in_read_only(server_factory) -> None:
    mcp = server_factory(read_only=True)
    for tool in ("hibob_submit_timeoff_request", "hibob_cancel_timeoff_request"):
        with pytest.raises(ToolError, match="Unknown tool"):
            await mcp.call_tool(tool, {})


async def test_hiding_people_data_leaves_only_the_policy_types(server_factory) -> None:
    names = {
        tool.name
        for tool in await server_factory(hide_people_data=True).list_tools()
        if "timeoff" in tool.name
    }
    assert names == {"hibob_list_timeoff_policy_types"}
