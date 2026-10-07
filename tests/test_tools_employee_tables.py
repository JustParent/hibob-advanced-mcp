"""hibob_update_employee: new rows in the work, employment and salary tables."""

from __future__ import annotations

import json
from datetime import date

from conftest import call_tool
from people_data import EMPLOYEE_ID, MANAGER_ID, PATTERN, SITES, FakePeople

TODAY = date.today().isoformat()


async def _update(mcp_server, **arguments) -> str:
    return await call_tool(mcp_server, "hibob_update_employee", arguments)


async def test_a_title_change_adds_a_work_row_that_carries_everything_else(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee="jane@x.com",
            changes={"Job title": "Head of Data"},
            effective_date="2026-11-01",
        )
    )
    expected = {
        "effectiveDate": "2026-11-01",
        "title": "102",
        "department": "201",
        "siteId": 2606110,
        "reportsTo": {"id": MANAGER_ID},
    }
    assert fake.writes == ["row:work"]
    assert fake.posted == [("work", expected)]
    assert result["status"] == "updated"
    assert result["applied"] == [
        {
            "field": "Work > Job title",
            "id": "work.title",
            "from": "101",
            "to": "Head of Data",
            "sent": "102",
            "via": "work row from 2026-11-01",
        }
    ]
    assert result["rows_added"] == [
        {"table": "work", "effective_date": "2026-11-01", "sent": expected}
    ]
    assert "unconfirmed" not in result
    stored = fake.tables["work"][-1]
    assert (stored["title"], stored["department"], stored["siteId"]) == (
        "102",
        "201",
        2606110,
    )
    assert stored["reportsTo"]["id"] == MANAGER_ID


async def test_manager_and_site_by_name(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={"Reports to": "Priya Patel", "Site": "madrid (demo)"},
            effective_date="2026-11-01",
        )
    )
    [(_, body)] = fake.posted
    assert body == {
        "effectiveDate": "2026-11-01",
        "title": "101",
        "department": "201",
        "siteId": 2606112,
        "reportsTo": {"id": "79"},
    }
    by_id = {entry["id"]: entry for entry in result["applied"]}
    assert by_id["work.reportsTo"]["from"] == "Sam Jones"
    assert by_id["work.reportsTo"]["sent"] == {"id": "79"}
    assert by_id["work.siteId"]["from"] == SITES[2606110]
    assert by_id["work.siteId"]["sent"] == 2606112


async def test_an_employment_change_keeps_the_derived_columns(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={"Employment contract": "part time"},
            effective_date="2026-11-01",
        )
    )
    assert fake.posted == [
        (
            "employment",
            {
                "effectiveDate": "2026-11-01",
                "contract": "Part time",
                "siteWorkingPattern": PATTERN,
                "actualWorkingPattern": PATTERN,
                "hoursInDayNotWorked": 8,
                "fte": 100,
                "weeklyHours": 40,
            },
        )
    ]
    assert result["applied"][0]["via"] == "employment row from 2026-11-01"
    assert "unconfirmed" not in result


async def test_a_first_salary_row_carries_the_amount_period_and_frequency(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={
                "Base salary": {"value": 50000, "currency": "gbp"},
                "Salary pay period": "annual",
                "Salary pay frequency": "monthly",
            },
            effective_date="2026-11-01",
        )
    )
    assert fake.posted == [
        (
            "salaries",
            {
                "effectiveDate": "2026-11-01",
                "base": {"value": 50000, "currency": "GBP"},
                "payPeriod": "Annual",
                "payFrequency": "Monthly",
            },
        )
    ]
    assert result["status"] == "updated"


async def test_a_bare_salary_takes_its_currency_from_the_row_it_copies(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    fake.add_row(
        "salaries",
        "2025-01-01",
        base={"value": 40000, "currency": "USD"},
        payPeriod="Annual",
        payFrequency="Monthly",
    )
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={"Base salary": 45000},
            effective_date="2026-06-01",
        )
    )
    assert fake.posted == [
        (
            "salaries",
            {
                "effectiveDate": "2026-06-01",
                "base": {"value": 45000, "currency": "USD"},
                "payPeriod": "Annual",
                "payFrequency": "Monthly",
            },
        )
    ]
    assert result["applied"][0]["sent"] == {"value": 45000, "currency": "USD"}


async def test_a_bare_salary_with_no_earlier_row_asks_for_the_currency(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={
                "Base salary": 50000,
                "Salary pay period": "Annual",
                "Salary pay frequency": "Monthly",
            },
            effective_date="2026-11-01",
        )
    )
    assert result["status"] == "needs_input"
    [question] = result["questions"]
    assert "currency" in question["question"]
    assert fake.writes == []


async def test_a_first_salary_row_without_a_period_or_frequency_asks_for_them(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={"Base salary": {"value": 1, "currency": "GBP"}},
            effective_date="2026-11-01",
        )
    )
    assert result["status"] == "needs_input"
    question = result["questions"][0]["question"]
    assert "pay period" in question
    assert "pay frequency" in question
    assert fake.writes == []


async def test_no_table_is_read_until_the_date_is_given(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server, employee=EMPLOYEE_ID, changes={"Job title": "Head of Data"}
        )
    )
    assert result["status"] == "needs_input"
    assert fake.reads == []


async def test_a_row_already_on_that_date_is_refused(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    fake.add_row("work", "2026-11-01", title="103", siteId=2606110)
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Job title": "Head of Data"},
        effective_date="2026-11-01",
    )
    assert text.startswith("Error:")
    assert "already has a work row dated 2026-11-01" in text
    assert "Nothing was written" in text
    assert fake.writes == []


async def test_a_date_before_the_first_row_is_refused(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Job title": "Head of Data"},
        effective_date="2020-01-01",
    )
    assert text.startswith("Error:")
    assert "no work row before 2020-01-01" in text
    assert fake.writes == []


async def test_a_past_date_copies_the_row_before_it_not_the_current_one(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    fake.add_row(
        "work",
        "2025-01-01",
        title="103",
        department="202",
        site=SITES[2606110],
        siteId=2606110,
    )
    asked = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={"Job title": "Head of Data"},
            effective_date="2024-06-01",
        )
    )
    assert asked["status"] == "needs_input"
    [question] = asked["questions"]
    assert question["argument"] == "allow_later_rows"
    assert "2025-01-01" in question["question"]
    assert question["later_rows"] == [
        {"effectiveDate": "2025-01-01", "columns": {"title": "103"}}
    ]
    assert fake.writes == []
    done = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={"Job title": "Head of Data"},
            effective_date="2024-06-01",
            allow_later_rows=True,
        )
    )
    assert done["status"] == "updated"
    [(_, body)] = fake.posted
    assert body["department"] == "201"
    assert body["title"] == "102"


async def test_a_later_row_that_already_holds_the_value_needs_no_question(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    fake.add_row("work", "2027-01-01", title="102", siteId=2606110)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={"Job title": "Head of Data"},
            effective_date="2026-11-01",
        )
    )
    assert result["status"] == "updated"
    assert fake.writes == ["row:work"]


async def test_restricted_columns_refuse_the_change(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    fake.restricted["work"] = {"no_view_history_permission": ["title"]}
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Job title": "Head of Data"},
        effective_date="2026-11-01",
    )
    assert text.startswith("Error:")
    assert "View history on title" in text
    assert "Nothing was written" in text
    assert fake.writes == []


async def test_a_change_to_the_value_already_held_adds_no_row(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={"Job title": "Analyst"},
            effective_date="2026-11-01",
        )
    )
    assert result["status"] == "unchanged"
    assert result["applied"] == []
    assert "no work row was added" in result["warnings"][0]
    assert fake.writes == []


async def test_a_reason_is_recorded_on_work_rows_but_not_on_salary_rows(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={
                "Job title": "Head of Data",
                "Base salary": {"value": 50000, "currency": "GBP"},
                "Salary pay period": "Annual",
                "Salary pay frequency": "Monthly",
            },
            effective_date="2026-11-01",
            reason="Promotion",
        )
    )
    bodies = dict(fake.posted)
    assert bodies["work"]["reason"] == "Promotion"
    assert "reason" not in bodies["salaries"]
    assert any("salary table has no reason column" in w for w in result["warnings"])
    assert fake.writes == ["row:work", "row:salaries"]


async def test_custom_columns_are_carried_and_changed_in_both_shapes(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    fake.tables["work"][0]["customColumns"] = {"column_55": "A"}
    await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Job title": "Head of Data"},
        effective_date="2026-11-01",
    )
    carried = fake.posted[-1][1]
    assert carried["customColumns"] == {"column_55": "A"}
    assert carried["column_55"] == "A"
    await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Cost centre": "B"},
        effective_date="2026-12-01",
    )
    changed = fake.posted[-1][1]
    assert changed["customColumns"] == {"column_55": "B"}
    assert changed["column_55"] == "B"


async def test_rows_go_before_plain_fields(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={"Mobile phone": "1", "Job title": "Head of Data"},
            effective_date=TODAY,
        )
    )
    assert fake.writes == ["row:work", "fields"]
    vias = {a["id"]: a["via"] for a in result["applied"]}
    assert vias == {
        "work.title": f"work row from {TODAY}",
        "home.mobilePhone": "field",
    }


async def test_a_future_date_cannot_schedule_plain_fields_beside_a_dated_change(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Mobile phone": "1", "Job title": "Head of Data"},
        effective_date="2030-01-01",
    )
    assert text.startswith("Error:")
    assert "Home > Personal mobile" in text or "Home > Mobile phone" in text
    assert "Job title" not in text
    assert fake.writes == []


async def test_a_failing_first_row_is_an_error_that_says_rows_are_only_added(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    fake.row_status["work"] = 400
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Job title": "Head of Data"},
        effective_date="2026-11-01",
    )
    assert text.startswith("Error:")
    assert "Duplicate effective date" in text
    assert "only adds rows" in text
    assert fake.writes == ["row:work"]


async def test_a_failing_second_table_is_partial(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    fake.row_status["employment"] = 400
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={"Job title": "Head of Data", "Employment contract": "Part time"},
            effective_date="2026-11-01",
        )
    )
    assert fake.writes == ["row:work", "row:employment"]
    assert result["status"] == "partial"
    assert [a["id"] for a in result["applied"]] == ["work.title"]
    assert result["failed"]["write"] == "employment row"
    assert result["not_sent"] == []


async def test_a_failing_field_write_after_a_row_is_partial(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    fake.put_status = 400
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={"Mobile phone": "1", "Job title": "Head of Data"},
            effective_date=TODAY,
        )
    )
    assert fake.writes == ["row:work", "fields"]
    assert result["status"] == "partial"
    assert result["failed"]["write"] == "fields"


async def test_a_column_hibob_blanks_is_reported_unconfirmed(
    mock_api, mcp_server, recorded_sleeps
) -> None:
    fake = FakePeople(mock_api)
    fake.drop_on_write["work"] = {"department"}
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={"Job title": "Head of Data"},
            effective_date="2026-11-01",
        )
    )
    assert result["status"] == "updated"
    assert result["unconfirmed"] == [
        {
            "field": "work row from 2026-11-01 > department",
            "sent": "201",
            "read": None,
        }
    ]
    assert "hibob_get_employee" in result["unconfirmed_note"]
    assert recorded_sleeps == [1.0, 3.0, 6.0]


async def test_a_future_dated_first_salary_row_warns_that_hibob_counts_it_as_current(
    mock_api, mcp_server
) -> None:
    """Checked live: with no earlier salary row, HiBob treats the first row as
    current whatever its date."""
    FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={
                "Base salary": {"value": 50000, "currency": "GBP"},
                "Salary pay period": "Annual",
                "Salary pay frequency": "Monthly",
            },
            effective_date="2099-01-01",
        )
    )
    assert result["status"] == "updated"
    assert any("counts a first salary row as current" in w for w in result["warnings"])


async def test_manager_and_change_type_can_be_set_on_a_work_row(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={
                "Job title": "Head of Data",
                "Manager": "Priya Patel",
                "Change type": "promotion",
            },
            effective_date="2026-11-01",
        )
    )
    assert fake.posted == [
        (
            "work",
            {
                "effectiveDate": "2026-11-01",
                "title": "102",
                "department": "201",
                "siteId": 2606110,
                "reportsTo": {"id": "79"},
                "workChangeType": "Promotion",
            },
        )
    ]
    assert result["status"] == "updated"
    assert "unconfirmed" not in result
    by_id = {a["id"]: a for a in result["applied"]}
    assert by_id["work.manager"]["from"] == "Sam Jones"
    assert by_id["work.workChangeType"]["to"] == "promotion"


async def test_the_manager_given_twice_under_two_names_is_refused(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Manager": "Priya Patel", "Reports to": "Sam Jones"},
        effective_date="2026-11-01",
    )
    assert text.startswith("Error:")
    assert "same work column" in text
    assert "Nothing was written" in text
    assert fake.writes == []
