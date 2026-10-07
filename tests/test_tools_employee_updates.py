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


async def test_writes_go_fields_then_start_date_then_email(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={
                "Email": "Janet@X.com",
                "Work > Start date": "2024-04-01",
                "Mobile phone": "1",
            },
            reason="Corrected",
        )
    )
    assert fake.writes == ["fields", "start date", "email"]
    assert json.loads(fake.start_date.calls.last.request.content) == {
        "startDate": "2024-04-01",
        "reason": "Corrected",
    }
    assert json.loads(fake.email.calls.last.request.content) == {"email": "janet@x.com"}
    assert result["status"] == "updated"
    vias = {a["id"]: a["via"] for a in result["applied"]}
    assert vias == {
        "home.mobilePhone": "field",
        "work.startDate": "start date endpoint",
        "root.email": "email endpoint",
    }
    assert any("verification" in w for w in result["warnings"])
    assert "unconfirmed" not in result


async def test_applied_changes_say_what_they_replaced(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server, employee=EMPLOYEE_ID, changes={"Shirt size": "Medium"}
        )
    )
    assert result["applied"][0]["from"] == "Large"


async def test_a_later_failure_is_partial_and_stops_the_rest(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    fake.start_date_status = 400
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={
                "Mobile phone": "1",
                "Work > Start date": "2024-04-01",
                "Email": "new@x.com",
            },
        )
    )
    assert fake.writes == ["fields", "start date"]
    assert result["status"] == "partial"
    assert [a["id"] for a in result["applied"]] == ["home.mobilePhone"]
    assert result["failed"]["write"] == "start date"
    assert "Bad start date" in result["failed"]["error"]
    assert result["not_sent"] == ["Basic info > Email"]


async def test_a_first_failure_is_an_error(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    fake.put_status = 400
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Mobile phone": "1", "Email": "new@x.com"},
    )
    assert text.startswith("Error:")
    assert fake.writes == ["fields"]


async def test_a_denied_write_names_the_categories_to_grant(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    fake.put_status = 403
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Mobile phone": "1", "Shirt size": "Medium"},
    )
    assert text.startswith("Error:")
    assert "Edit on Home, Work" in text


async def test_304_means_nothing_changed(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    fake.put_status = 304
    result = json.loads(
        await _update(mcp_server, employee=EMPLOYEE_ID, changes={"Mobile phone": "1"})
    )
    assert result["status"] == "unchanged"
    assert result["applied"] == []
    assert "changed nothing" in result["warnings"][0]


async def test_same_email_in_other_case_is_unchanged(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await _update(mcp_server, employee=EMPLOYEE_ID, changes={"Email": "JANE@x.com"})
    )
    assert result["status"] == "unchanged"


async def test_a_change_hibob_ignores_is_unconfirmed_after_rereading(
    mock_api, mcp_server, recorded_sleeps
) -> None:
    fake = FakePeople(mock_api)
    fake.apply_writes = False
    result = json.loads(
        await _update(mcp_server, employee=EMPLOYEE_ID, changes={"Mobile phone": "1"})
    )
    assert result["status"] == "updated"
    assert result["unconfirmed"] == [
        {"field": "Home > Mobile phone", "sent": "1", "read": "07700 900000"}
    ]
    assert "Edit on Home" in result["unconfirmed_note"]
    assert recorded_sleeps == [1.0, 3.0, 6.0]


async def test_a_change_read_back_at_once_needs_no_wait(
    mock_api, mcp_server, recorded_sleeps
) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await _update(mcp_server, employee=EMPLOYEE_ID, changes={"Buddy": "Sam Jones"})
    )
    assert "unconfirmed" not in result
    assert recorded_sleeps == []


async def test_dated_changes_without_a_date_ask_for_one_and_write_nothing(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={
                "Job title": "Head of Data",
                "Reports to": "Sam Jones",
                "Mobile phone": "1",
            },
        )
    )
    assert result["status"] == "needs_input"
    [question] = result["questions"]
    assert question["argument"] == "effective_date"
    assert "Job title, Reports to" in question["question"]
    assert "Jane Smith" in question["question"]
    assert question["applies_to"] == ["Work > Job title", "Work > Reports to"]
    assert fake.writes == []


async def test_the_date_question_comes_with_the_other_questions(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={"Job title": "Head of Data", "Shirt size": "Large"},
        )
    )
    assert {q.get("argument") for q in result["questions"]} == {
        "effective_date",
        "changes",
    }


async def test_site_is_the_dated_site_field(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server, employee=EMPLOYEE_ID, changes={"Site": "Madrid (Demo)"}
        )
    )
    assert result["questions"][0]["applies_to"] == ["Work > Site"]


async def test_a_field_given_twice_is_refused(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Mobile phone": "1", "home.mobilePhone": "2"},
    )
    assert text.startswith("Error:")
    assert "given twice" in text
    assert "Nothing was written" in text
    assert fake.writes == []


async def test_a_dated_change_with_a_date_is_not_written_until_rows_are_supported(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Job title": "Head of Data"},
        effective_date="2026-11-01",
    )
    assert text.startswith("Error:")
    assert "not supported yet" in text
    assert fake.writes == []
