"""Rows of the work, employment and salary tables: base row, new row, checks."""

from __future__ import annotations

import pytest

from hibob_advanced_mcp.employee_tables import (
    TABLES,
    build_row,
    compare_row,
    later_conflicts,
    restricted_message,
    row_value,
    split_rows,
    to_wire,
)

WORK = TABLES["work"]
EMPLOYMENT = TABLES["employment"]
SALARY = TABLES["salary"]
MANAGER = {
    "id": "5",
    "firstName": "Sam",
    "surname": "Jones",
    "email": "s@x.com",
    "displayName": "Sam Jones",
}
PATTERN = {"workingPatternType": "hourly", "hoursPerDay": 8, "workingPatternId": 0}


def _row(day: str, **columns):
    return {
        "id": int(day.replace("-", "")),
        "effectiveDate": day,
        "endEffectiveDate": None,
        "isCurrent": False,
        "canBeDeleted": True,
        "change": {"reason": None, "changedBy": None, "changedById": "1"},
        "creationDate": None,
        "modificationDate": day,
        "activeEffectiveDate": day,
        **columns,
    }


WORK_BASE = _row(
    "2024-03-01",
    workChangeType="New Employee",
    title="Analyst",
    department="Data",
    site="London (Demo)",
    siteId=2606110,
    reportsTo=MANAGER,
    customColumns={},
)
EMPLOYMENT_BASE = _row(
    "2024-03-01",
    contract="Full time",
    type=None,
    calendarId=2657450,
    calendarName="Canada bank holidays",
    standardWorkingPattern={"workingPatternId": 7},
    standardWorkingPatternId=7,
    siteWorkingPattern=PATTERN,
    actualWorkingPattern=PATTERN,
    hoursInDayNotWorked=8,
    fte=100,
    weeklyHours=40,
    customColumns={},
)


def test_split_rows_finds_the_base_the_same_day_and_the_later_rows() -> None:
    rows = [_row("2026-06-01"), _row("2024-03-01"), _row("2025-01-01"), {"id": 9}]
    base, same, later = split_rows(rows, "2025-06-01", WORK)
    assert base is not None and base["effectiveDate"] == "2025-01-01"
    assert same is None
    assert [r["effectiveDate"] for r in later] == ["2026-06-01"]
    base, same, later = split_rows(rows, "2025-01-01", WORK)
    assert base is not None and base["effectiveDate"] == "2024-03-01"
    assert same is not None and same["effectiveDate"] == "2025-01-01"


def test_split_rows_before_the_first_row_has_no_base() -> None:
    base, same, later = split_rows([_row("2024-03-01")], "2020-01-01", WORK)
    assert (base, same, len(later)) == (None, None, 1)


def test_two_rows_on_the_base_date_leave_no_single_row_to_copy() -> None:
    rows = [_row("2024-03-01"), {**_row("2024-03-01"), "id": 2}]
    with pytest.raises(ValueError, match="no single row to copy"):
        split_rows(rows, "2025-01-01", WORK)


def test_a_new_work_row_carries_every_other_column() -> None:
    body = build_row(WORK_BASE, {"title": "Head of Data"}, "2026-11-01", WORK)
    assert body == {
        "effectiveDate": "2026-11-01",
        "title": "Head of Data",
        "department": "Data",
        "siteId": 2606110,
        "reportsTo": {"id": "5"},
    }


def test_a_new_employment_row_keeps_derived_columns_but_not_the_labels() -> None:
    body = build_row(
        EMPLOYMENT_BASE, {"contract": "Part time"}, "2026-11-01", EMPLOYMENT
    )
    assert body == {
        "effectiveDate": "2026-11-01",
        "contract": "Part time",
        "calendarId": 2657450,
        "standardWorkingPatternId": 7,
        "siteWorkingPattern": PATTERN,
        "actualWorkingPattern": PATTERN,
        "hoursInDayNotWorked": 8,
        "fte": 100,
        "weeklyHours": 40,
    }


def test_zero_is_kept_and_none_is_dropped() -> None:
    body = build_row(_row("2024-03-01", fte=0, type=None), {}, "2026-01-01", EMPLOYMENT)
    assert body == {"effectiveDate": "2026-01-01", "fte": 0}


def test_a_changed_id_replaces_a_stale_label_it_was_read_beside() -> None:
    base = _row("2024-03-01", site="London (Demo)", title="Analyst")
    body = build_row(base, {"siteId": 2606111}, "2026-11-01", WORK)
    assert body == {
        "effectiveDate": "2026-11-01",
        "title": "Analyst",
        "siteId": 2606111,
    }


def test_custom_columns_go_in_both_shapes_and_changes_win() -> None:
    base = _row(
        "2024-03-01", title="Analyst", customColumns={"column_1": "A", "column_2": "B"}
    )
    body = build_row(base, {"customColumns.column_2": "C"}, "2026-11-01", WORK)
    assert body["customColumns"] == {"column_1": "A", "column_2": "C"}
    assert (body["column_1"], body["column_2"]) == ("A", "C")


def test_reason_only_where_the_table_has_a_reason_column() -> None:
    assert (
        build_row(WORK_BASE, {}, "2026-11-01", WORK, "Promotion")["reason"]
        == "Promotion"
    )
    amount = {"base": {"value": 1, "currency": "GBP"}}
    assert "reason" not in build_row(None, amount, "2026-11-01", SALARY, "Raise")


def test_a_first_row_is_just_the_changes() -> None:
    changes = {"base": {"value": 50000, "currency": "GBP"}, "payPeriod": "Annual"}
    assert build_row(None, changes, "2026-11-01", SALARY) == {
        **changes,
        "effectiveDate": "2026-11-01",
    }


def test_row_value_shows_the_names_hibob_gives() -> None:
    assert row_value(WORK_BASE, "reportsTo") == "Sam Jones"
    assert row_value(WORK_BASE, "siteId") == "London (Demo)"
    assert row_value(WORK_BASE, "title") == "Analyst"
    custom = _row("2024-03-01", customColumns={"column_1": "A"})
    assert row_value(custom, "customColumns.column_1") == "A"
    readable = _row("2024-03-01", title="101", humanReadable={"title": "Analyst"})
    assert row_value(readable, "title") == "Analyst"
    assert row_value(None, "title") is None


def test_a_later_row_conflicts_only_where_it_holds_a_different_value() -> None:
    later = [
        _row("2027-01-01", title="Analyst", department="Data"),
        _row("2028-01-01", title="Head of Data"),
    ]
    assert later_conflicts(later, {"title": "Head of Data"}) == [
        {"effectiveDate": "2027-01-01", "columns": {"title": "Analyst"}}
    ]
    assert later_conflicts(later[1:], {"title": "Head of Data"}) == []


def test_a_later_row_with_another_manager_conflicts() -> None:
    later = [_row("2027-01-01", reportsTo=MANAGER)]
    assert later_conflicts(later, {"reportsTo": {"id": "9"}}) != []
    assert later_conflicts(later, {"reportsTo": {"id": "5"}}) == []


def test_compare_row_reports_a_column_hibob_blanked() -> None:
    sent = {
        "effectiveDate": "2026-11-01",
        "title": "Head of Data",
        "department": "Data",
        "siteId": 2606110,
        "reportsTo": {"id": "5"},
        "reason": "x",
    }
    read = _row(
        "2026-11-01",
        title="Head of Data",
        department=None,
        siteId=2606110,
        reportsTo=MANAGER,
    )
    assert compare_row(sent, read) == [
        {"column": "department", "sent": "Data", "read": None}
    ]


def test_compare_row_skips_derived_columns_and_checks_custom_columns_once() -> None:
    sent = {
        "effectiveDate": "2026-11-01",
        "fte": 100,
        "weeklyHours": 40,
        "customColumns": {"column_1": "A"},
        "column_1": "A",
    }
    read = _row("2026-11-01", fte=0, weeklyHours=0, customColumns={"column_1": "B"})
    assert compare_row(sent, read) == [
        {"column": "customColumns.column_1", "sent": "A", "read": "B"}
    ]


def test_compare_row_compares_people_by_id_and_amounts_by_value() -> None:
    sent = {"reportsTo": {"id": "9"}, "base": {"value": 5000, "currency": "GBP"}}
    read = _row(
        "2026-11-01", reportsTo=MANAGER, base={"value": 5000.0, "currency": "GBP"}
    )
    assert compare_row(sent, read) == [
        {"column": "reportsTo", "sent": {"id": "9"}, "read": MANAGER}
    ]


def test_restricted_message_names_the_columns_and_what_to_grant() -> None:
    assert restricted_message(WORK, {}) is None
    assert restricted_message(WORK, {"no_view_permission": []}) is None
    assert restricted_message(WORK, "nonsense") is None
    message = restricted_message(
        WORK,
        {
            "no_view_permission": ["title"],
            "no_view_history_permission": ["department", "site"],
        },
    )
    assert message is not None
    assert "View on title" in message
    assert "View history on department, site" in message
    assert "work table" in message


@pytest.mark.parametrize(
    ("wire", "given", "expected"),
    [
        ("int", "2606111", 2606111),
        ("int", 5, 5),
        ("employee", "79", {"id": "79"}),
        ("text", "x", "x"),
        ("amount", {"value": 1, "currency": None}, {"value": 1, "currency": None}),
    ],
)
def test_to_wire(wire: str, given, expected) -> None:
    assert to_wire(wire, given, "Site") == expected


def test_to_wire_refuses_a_non_numeric_id() -> None:
    with pytest.raises(ValueError, match="numeric"):
        to_wire("int", "Madrid", "Work > Site")


def test_an_address_reason_is_nested_under_change() -> None:
    body = build_row(
        {"city": "Leeds", "line1": "1 Old Street"},
        {"city": "York"},
        "2026-11-01",
        TABLES["address"],
        "Moved house",
    )
    assert body == {
        "city": "York",
        "line1": "1 Old Street",
        "effectiveDate": "2026-11-01",
        "change": {"reason": "Moved house"},
    }
