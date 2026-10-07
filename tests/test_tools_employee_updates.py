"""hibob_update_employee: plain fields, questions and refusals."""

from __future__ import annotations

import json
from datetime import date

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from conftest import call_tool
from people_data import EMPLOYEE_ID, MANAGER_ID, FakePeople


async def _update(mcp_server, **arguments) -> str:
    return await call_tool(mcp_server, "hibob_update_employee", arguments)


async def test_plain_fields_go_in_one_nested_put(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    text = await _update(
        mcp_server,
        employee="jane@x.com",
        changes={
            "Mobile phone": "07700 900123",
            "First name": "Janet",
            "Shirt size": "medium",
            "Languages": ["Spanish", "French"],
            "Buddy": "Sam Jones",
        },
    )
    assert fake.put.call_count == 1
    assert json.loads(fake.put.calls.last.request.content) == {
        "firstName": "Janet",
        "home": {"mobilePhone": "07700 900123"},
        "work": {"custom": {"field_100": "M", "field_400": MANAGER_ID}},
        "about": {"custom": {"field_300": ["1", "2"]}},
    }
    result = json.loads(text)
    assert result["status"] == "updated"
    shirt = next(a for a in result["applied"] if a["id"] == "work.custom.field_100")
    assert (shirt["to"], shirt["sent"], shirt["via"]) == ("medium", "M", "field")


async def test_employee_given_as_a_number(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    text = await _update(
        mcp_server, employee=int(EMPLOYEE_ID), changes={"Mobile phone": "1"}
    )
    assert json.loads(text)["status"] == "updated"
    assert fake.put.call_count == 1


async def test_questions_are_collected_and_nothing_is_written(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={
            "Start date": "2026-11-01",
            "Shirt sise": "Large",
            "Shirt size": "Large",
            "Buddy": "Alex Lee",
            "Bonus target": 5000,
            "Languages": "Spanish, French",
        },
    )
    result = json.loads(text)
    assert result["status"] == "needs_input"
    by_key = {q["key"]: q for q in result["questions"]}
    assert {c["label"] for c in by_key["Start date"]["candidates"]} == {
        "Work > Start date",
        "Home > Start date",
    }
    assert "Work > Shirt size" in [
        c["label"] for c in by_key["Shirt sise"]["candidates"]
    ]
    assert {c["id"] for c in by_key["Shirt size"]["candidates"]} == {"L", "L2"}
    assert len(by_key["Buddy"]["candidates"]) == 2
    assert "currency" in by_key["Bonus target"]["question"]
    assert by_key["Languages"]["candidates"]
    assert fake.writes == []


async def test_an_ambiguous_employee_is_a_question(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(mcp_server, employee="Alex Lee", changes={"Mobile phone": "1"})
    )
    assert result["status"] == "needs_input"
    assert result["questions"][0]["argument"] == "employee"
    assert fake.writes == []


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"Job title": "Head of Data"}, "not supported yet"),
        ({"City": "Leeds"}, "cannot be changed"),
        ({"Status": "Inactive"}, "cannot be changed"),
        ({"Mobile phone": None}, "null"),
        ({"Work > Start date": "01/11/2026"}, "YYYY-MM-DD"),
        ({"Email": "not-an-email"}, "not an email address"),
    ],
)
async def test_refusals_write_nothing(mock_api, mcp_server, changes, expected) -> None:
    fake = FakePeople(mock_api)
    text = await _update(mcp_server, employee=EMPLOYEE_ID, changes=changes)
    assert text.startswith("Error:")
    assert expected in text
    assert "Nothing was written" in text
    assert fake.writes == []


async def test_unknown_employee_is_an_error(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    text = await _update(
        mcp_server, employee="nobody@x.com", changes={"Mobile phone": "1"}
    )
    assert text.startswith("Error:")
    assert "No employee found" in text


async def test_a_future_effective_date_cannot_schedule_plain_fields(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Mobile phone": "1"},
        effective_date="2099-01-01",
    )
    assert text.startswith("Error:")
    assert "Home > Mobile phone" in text
    assert "immediately" in text
    assert fake.writes == []


async def test_todays_effective_date_is_fine_for_plain_fields(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Mobile phone": "1"},
        effective_date=date.today().isoformat(),
    )
    assert json.loads(text)["status"] == "updated"
    assert fake.put.call_count == 1


async def test_empty_changes_are_refused(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    text = await _update(mcp_server, employee=EMPLOYEE_ID, changes={})
    assert text.startswith("Error:")


async def test_update_is_absent_in_read_only_mode(server_factory) -> None:
    with pytest.raises(ToolError, match="Unknown tool"):
        await call_tool(
            server_factory(read_only=True),
            "hibob_update_employee",
            {"employee": EMPLOYEE_ID, "changes": {"Mobile phone": "1"}},
        )
