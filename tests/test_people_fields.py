"""people_fields: what an employee field is and how a change to it is written."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hibob_advanced_mcp.people_fields import (
    canonical_field_id,
    describe_field,
    find_fields,
    nearest_fields,
    normalize_custom_tables,
    normalize_people_fields,
    read_field,
    route_for,
)
from people_data import CUSTOM_TABLES, FIELDS

ALL = normalize_people_fields(FIELDS)
BY_ID = {field.id: field for field in ALL}
REAL_FIELDS = Path(__file__).parent / "fixtures" / "people" / "fields.json"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("work.title", "work.title"),
        ("/work/title", "work.title"),
        ("firstName", "root.firstName"),
        ("/root/email", "root.email"),
        (" root.id ", "root.id"),
    ],
)
def test_canonical_field_id(given: str, expected: str) -> None:
    assert canonical_field_id(given) == expected


def test_normalize_reads_label_category_list_and_flags() -> None:
    title = BY_ID["work.title"]
    assert (title.label, title.category, title.type, title.list_id) == (
        "Job title",
        "Work",
        "list",
        "title",
    )
    assert title.historical is True
    assert title.qualified_label == "Work > Job title"
    assert BY_ID["root.email"].json_path == "email"


def test_normalize_accepts_a_wrapped_list_and_skips_junk() -> None:
    fields = normalize_people_fields({"fields": [FIELDS[2], "junk", {"name": "no id"}]})
    assert [field.id for field in fields] == ["root.email"]


def test_find_by_id_in_any_spelling_or_by_label_ignoring_case() -> None:
    assert [f.id for f in find_fields(ALL, "/work/title")] == ["work.title"]
    assert [f.id for f in find_fields(ALL, "JOB TITLE")] == ["work.title"]
    assert [f.id for f in find_fields(ALL, "email")] == ["root.email"]


def test_a_shared_label_returns_both_and_a_qualified_label_picks_one() -> None:
    assert {f.id for f in find_fields(ALL, "Start date")} == {
        "work.startDate",
        "home.custom.field_200",
    }
    assert [f.id for f in find_fields(ALL, "home > start date")] == [
        "home.custom.field_200"
    ]


def test_work_site_is_an_alias_of_the_dated_site_id() -> None:
    """work.site is plain text showing the work row's site; it is written as
    siteId, so it is not offered separately and its ID finds siteId."""
    assert "work.site" not in BY_ID
    assert [f.id for f in find_fields(ALL, "Site")] == ["work.siteId"]
    assert [f.id for f in find_fields(ALL, "work.site")] == ["work.siteId"]


def test_variable_pay_fields_say_they_are_records() -> None:
    reason = route_for(BY_ID["payroll.variable.Bonus.amount"]).reason
    assert "variable pay" in str(reason)


def test_find_refuses_an_empty_name() -> None:
    with pytest.raises(ValueError):
        find_fields(ALL, "  ")


def test_nearest_fields_offers_labels_sharing_a_word() -> None:
    assert "work.title" in [f.id for f in nearest_fields(ALL, "title")]


@pytest.mark.parametrize(
    ("field_id", "kind", "table"),
    [
        ("home.mobilePhone", "field", None),
        ("work.custom.field_100", "field", None),
        ("root.email", "email", None),
        ("work.startDate", "start_date", None),
        ("work.title", "dated", "work"),
        ("work.reportsTo", "dated", "work"),
        ("payroll.salary.payment", "dated", "salary"),
        ("address.city", "not_writable", None),
        ("internal.status", "not_writable", None),
        ("root.displayName", "not_writable", None),
        ("root.id", "not_writable", None),
        ("personal.custom.field_600", "not_writable", None),
        ("work.siteId", "dated", "work"),
        ("work.tenureDuration", "not_writable", None),
        ("peopleAnalytics.teamSizeRiskIndicator", "not_writable", None),
        ("payroll.variable.Bonus.amount", "not_writable", None),
    ],
)
def test_route_for(field_id: str, kind: str, table: str | None) -> None:
    route = route_for(BY_ID[field_id])
    assert (route.kind, route.table) == (kind, table)
    assert bool(route.reason) is (kind == "not_writable")


def test_describe_field_says_how_it_is_written() -> None:
    assert describe_field(BY_ID["work.department"]) == {
        "id": "work.department",
        "label": "Department",
        "category": "Work",
        "type": "list",
        "write": "dated",
        "list": "department",
        "table": "work",
    }
    assert describe_field(BY_ID["address.city"])["reason"]


def test_custom_tables_keep_columns_and_required_flags() -> None:
    assert normalize_custom_tables(CUSTOM_TABLES) == [
        {
            "id": "about__table_1",
            "name": "Certifications",
            "category": "about",
            "columns": [
                {
                    "id": "column_1",
                    "label": "Certificate",
                    "type": "list",
                    "required": True,
                    "list": "certs",
                },
                {
                    "id": "column_2",
                    "label": "Expires",
                    "type": "date",
                    "required": False,
                },
            ],
        }
    ]


def test_read_field_from_a_nested_record_with_display_labels() -> None:
    record = {
        "email": "jane@x.com",
        "work": {"title": "101"},
        "humanReadable": {"work": {"title": "Analyst"}},
    }
    assert read_field(record, "work.title") == ("101", "Analyst")
    assert read_field(record, "root.email") == ("jane@x.com", None)


def test_read_field_from_slash_keys() -> None:
    record = {
        "/work/title": {"value": "101", "humanReadable": "Analyst"},
        "/root/id": {"value": "7"},
    }
    assert read_field(record, "work.title") == ("101", "Analyst")
    assert read_field(record, "root.id") == ("7", None)


def test_read_field_missing_or_not_a_record() -> None:
    assert read_field({}, "work.title") == (None, None)
    assert read_field(None, "work.title") == (None, None)


@pytest.mark.skipif(not REAL_FIELDS.exists(), reason="sandbox fixture not captured")
def test_real_metadata_fixture() -> None:
    real = normalize_people_fields(json.loads(REAL_FIELDS.read_text()))
    fields = {f.id: f for f in real}
    assert route_for(fields["work.title"]).table == "work"
    assert route_for(fields["work.reportsTo"]).table == "work"
    assert route_for(fields["root.email"]).kind == "email"
    assert route_for(fields["home.mobilePhone"]).kind == "field"
    assert route_for(fields["work.tenureDuration"]).kind == "not_writable"
    assert route_for(fields["peopleAnalytics.ageRiskIndicator"]).kind == "not_writable"
    assert route_for(fields["internal.status"]).kind == "not_writable"
    # Live: PUT answers 304 to these whatever the value's type; they follow
    # the job profile.
    for job_field in ("employee.jobRoleId", "employee.jobFamilyId"):
        assert route_for(fields[job_field]).kind == "not_writable"
    assert [f.id for f in find_fields(real, "Site")] == ["work.siteId"]
    assert all(f.label for f in real)
    assert route_for(fields["payroll.salary.payment"]).column == "base"
    assert route_for(fields["work.siteId"]).wire == "int"
    # "Manager" is the same value as "Reports to", and "Change type" is an
    # ordinary dated column: neither is calculated.
    assert route_for(fields["work.manager"]).column == "reportsTo"
    assert route_for(fields["work.workChangeType"]).column == "workChangeType"
    assert route_for(fields["payroll.employment.calendarId"]).wire == "int"
    for derived in (
        "payroll.salary.yearlyPayment",
        "payroll.salary.monthlyPayment",
        "payroll.employment.hoursInDayNotWorked",
        "payroll.employment.personalWorkingPatternType",
        "payroll.employment.standardWorkingPattern.workingPatternId",
    ):
        assert route_for(fields[derived]).kind == "not_writable", derived
    for field in real:
        route = route_for(field)
        if route.kind == "dated":
            assert route.table and route.column and route.wire, field.id


@pytest.mark.parametrize(
    ("field_id", "table", "column", "wire"),
    [
        ("work.title", "work", "title", "text"),
        ("work.department", "work", "department", "text"),
        ("work.siteId", "work", "siteId", "int"),
        ("work.reportsTo", "work", "reportsTo", "employee"),
        ("work.manager", "work", "reportsTo", "employee"),
        ("work.workChangeType", "work", "workChangeType", "text"),
        ("work.customColumns.column_55", "work", "customColumns.column_55", "text"),
        ("payroll.employment.contract", "employment", "contract", "text"),
        ("payroll.employment.type", "employment", "type", "text"),
        ("payroll.employment.calendarId", "employment", "calendarId", "int"),
        ("payroll.salary.payment", "salary", "base", "amount"),
        ("payroll.salary.payPeriod", "salary", "payPeriod", "text"),
        ("payroll.salary.payFrequency", "salary", "payFrequency", "text"),
    ],
)
def test_dated_fields_say_which_table_column_they_are(
    field_id: str, table: str, column: str, wire: str
) -> None:
    route = route_for(BY_ID[field_id])
    assert (route.kind, route.table, route.column, route.wire) == (
        "dated",
        table,
        column,
        wire,
    )


@pytest.mark.parametrize(
    "field_id",
    [
        "payroll.employment.fte",
        "payroll.employment.personalWorkingPatternType",
        "payroll.employment.workingPattern",
        "payroll.employment.standardWorkingPattern.workingPatternId",
        "payroll.salary.yearlyPayment",
    ],
)
def test_employment_and_salary_fields_that_cannot_be_written(field_id: str) -> None:
    route = route_for(BY_ID[field_id])
    assert route.kind == "not_writable"
    assert route.reason
