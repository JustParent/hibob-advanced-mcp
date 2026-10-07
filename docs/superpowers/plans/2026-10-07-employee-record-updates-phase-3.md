# Employee Record Updates (Phase 3: dated rows) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `hibob_update_employee` change job title, department, site, manager, employment terms and salary by adding a new row to HiBob's work, employment or salary table, carrying every other column forward and asking for any missing effective date.

**Architecture:** `people_fields.py` routes each dated field to a table column; a new pure module `employee_tables.py` decides which row to copy and builds and checks the new row; `employee_updates.py` reads the tables, asks or refuses before the first write, then writes rows first (work, employment, salary), plain fields next, and reads every row back.

**Tech Stack:** Python ≥3.10, `mcp` FastMCP, `httpx`, `pydantic`, `pytest` + `pytest-asyncio` (auto mode) + `respx`, `ruff`, `mypy`.

**Spec:** `docs/superpowers/specs/2026-10-07-employee-record-updates-design.md` (see "Write mechanics → A new dated row" and "Findings from the demo tenant"). Phase 4 (`hibob_add_employee_record`) is not in this plan.

## Global Constraints

- Python 3.10 and 3.12 both run in CI: no 3.11+ APIs.
- `ruff check .`, `ruff format --check .` and `mypy` must pass; run `ruff format .` before each commit.
- Work directly on `master` (the user said not to make a branch); commit after each task; do not push.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Tool results are JSON from `json.dumps(payload, indent=2, default=str)`, or a string beginning `Error:` from `errors.format_exception`.
- Writes are never retried; reads go through `client.get` / `client.search`.
- Matching is exact, ignoring case; ambiguity returns candidates; nothing is guessed.
- An effective date is never defaulted; `null` is refused as a value.
- Every check and read runs before the first write; a refusal or question writes nothing.
- No new tools: the tool counts stay at 20 read and 12 write (32 in all), so `tests/test_stdio_server.py` and the gating sets do not change.
- Live checks (Task 5) write only to `david@harriethq.com` on the Harriet Hibob Demo tenant (justparent integration 3), only dated 2030, and nothing is ever deleted by this plan or the tool.
- Test commands use the repo venv: `.venv/bin/pytest`, `.venv/bin/ruff`, `.venv/bin/mypy`.

## Review Focus

1. **A past effective date between two existing rows** must copy the row before that date, not the row in force today: copying today's row would restore stale values. Pinned in Task 4 (`test_a_past_date_copies_the_row_before_it_not_the_current_one`).
2. **A bare salary amount for someone with no salary row** must ask for the currency, not guess one. Pinned in Task 4.
3. **The same field given twice (by label and by ID)** must be refused, not resolved by "last one wins". Pinned in Task 3.
4. **HiBob silently blanks a copied column on write**: the result must list it under `unconfirmed` rather than report a clean update. Pinned in Task 4.
5. **A manager changed to a different person** must compare by ID: two different `{"id": ...}` dicts must never compare equal. Pinned in Task 2 (`same_value`, `compare_row`).

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/hibob_advanced_mcp/people_fields.py` (modify) | Route each dated field to a table column; refuse derived ones. |
| `src/hibob_advanced_mcp/employee_rows.py` (modify) | `same_value` compares `{"id"}` dicts and nested dicts correctly. |
| `src/hibob_advanced_mcp/employee_tables.py` (new, pure) | Tables, base-row choice, building and checking a row. |
| `src/hibob_advanced_mcp/employee_values.py` (modify) | A bare amount may be accepted where the row supplies the currency. |
| `src/hibob_advanced_mcp/employee_updates.py` (modify) | Plan dated changes, read tables, write and confirm rows. |
| `src/hibob_advanced_mcp/errors.py` (modify) | Explain a duplicate-effective-date 400. |
| `tests/people_data.py` (modify) | Fields, lists and table behaviour on the fake HiBob. |
| `tests/test_people_fields.py`, `tests/test_employee_values.py`, `tests/test_employee_tables.py` (new), `tests/test_tools_employee_updates.py`, `tests/test_tools_employee_tables.py` (new), `tests/test_errors.py` (modify) | Tests. |
| `README.md`, spec, `tests/fixtures/people/observations.md` (modify) | Documentation and live findings. |

---

### Task 1: Route dated columns

**Files:**
- Modify: `src/hibob_advanced_mcp/people_fields.py`
- Modify: `tests/people_data.py` (the `FIELDS` list)
- Test: `tests/test_people_fields.py`

**Interfaces:**
- Produces: `Route.column: str | None`, `Route.wire: str | None` (`"text"`, `"int"`, `"employee"` or `"amount"`); `DATED_COLUMNS: dict[str, tuple[str, str, str]]`; a `dated` route now always has `table`, `column` and `wire`.

- [ ] **Step 1: Add fields to the test data**

In `tests/people_data.py`, add these entries to `FIELDS` directly after the `payroll.variable.Bonus.amount` entry:

```python
    _field(
        "payroll.employment.contract",
        "Contract",
        "Employment",
        "list",
        list_id="employmentstatus",
        historical=True,
    ),
    _field(
        "payroll.employment.type",
        "Employment type",
        "Employment",
        "list",
        list_id="payrollEmploymentType",
        historical=True,
    ),
    _field(
        "payroll.employment.calendarId",
        "Holiday calendar ID",
        "Employment",
        "list_id",
        list_id="calendar",
        historical=True,
    ),
    _field("payroll.employment.fte", "FTE", "Employment", "number", historical=True),
    _field(
        "payroll.employment.personalWorkingPatternType",
        "Personal working pattern type",
        "Employment",
        "list",
        list_id="personalWorkingPatternTypes",
        historical=True,
    ),
    _field(
        "payroll.employment.workingPattern",
        "Working pattern",
        "Employment",
        "working_pattern",
        historical=True,
    ),
    _field(
        "payroll.employment.standardWorkingPattern.workingPatternId",
        "Full time working pattern",
        "Employment",
        "list_id",
        list_id="workingPattern_entity_list",
    ),
    _field(
        "payroll.salary.payPeriod",
        "Pay period",
        "Payroll",
        "list",
        list_id="payPeriod",
        historical=True,
    ),
    _field(
        "payroll.salary.payFrequency",
        "Pay frequency",
        "Payroll",
        "list",
        list_id="payFrequency",
        historical=True,
    ),
    _field("payroll.salary.yearlyPayment", "Yearly payment", "Payroll", "currency"),
    _field(
        "work.customColumns.column_55",
        "Cost centre",
        "Work",
        "text",
        historical=True,
    ),
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_people_fields.py`:

```python
@pytest.mark.parametrize(
    ("field_id", "table", "column", "wire"),
    [
        ("work.title", "work", "title", "text"),
        ("work.department", "work", "department", "text"),
        ("work.siteId", "work", "siteId", "int"),
        ("work.reportsTo", "work", "reportsTo", "employee"),
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
```

Add to the end of `test_real_metadata_fixture` in the same file:

```python
    assert route_for(fields["payroll.salary.payment"]).column == "base"
    assert route_for(fields["work.siteId"]).wire == "int"
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
```

- [ ] **Step 3: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_people_fields.py -q 2>&1 | tail -8`
Expected: failures: `Route` has no `column`/`wire`, and the derived fields still route as `field` or `dated`.

- [ ] **Step 4: Implement**

In `src/hibob_advanced_mcp/people_fields.py`:

Add `import re` below `from dataclasses import dataclass`.

Replace the `DATED_TABLE_PREFIXES` block (the comment and tuple) with:

```python
# Columns of the effective-dated tables that a change can be written to: field
# ID -> (table, column in the table's write body, how the value is sent).
DATED_COLUMNS = {
    "work.title": ("work", "title", "text"),
    "work.department": ("work", "department", "text"),
    "work.siteId": ("work", "siteId", "int"),
    "work.reportsTo": ("work", "reportsTo", "employee"),
    "payroll.employment.contract": ("employment", "contract", "text"),
    "payroll.employment.type": ("employment", "type", "text"),
    "payroll.employment.salaryPayType": ("employment", "salaryPayType", "text"),
    "payroll.employment.flsaCode": ("employment", "flsaCode", "text"),
    "payroll.employment.calendarId": ("employment", "calendarId", "int"),
    "payroll.salary.payment": ("salary", "base", "amount"),
    "payroll.salary.payPeriod": ("salary", "payPeriod", "text"),
    "payroll.salary.payFrequency": ("salary", "payFrequency", "text"),
}
# A custom column of one of those tables (an inferred ID pattern: no tenant
# checked so far has one). It is written under customColumns.
CUSTOM_COLUMN = re.compile(
    r"(work|payroll\.employment|payroll\.salary)\.customColumns\.(column_\w+)"
)
CUSTOM_COLUMN_TABLES = {
    "work": "work",
    "payroll.employment": "employment",
    "payroll.salary": "salary",
}
# Dated fields HiBob takes but this tool cannot set yet.
UNSUPPORTED_DATED = {
    "payroll.employment.personalWorkingPatternType": (
        "working patterns are not supported yet"
    ),
}
# Fields outside the dated tables' own history that HiBob derives from a
# table's rows (seen in a live tenant's metadata): shown, never written.
DERIVED_PREFIXES = (
    ("payroll.salary.", "HiBob derives it from the salary rows"),
    ("payroll.employment.", "HiBob derives it from the employment rows"),
)
```

Add `"payroll.employment.hoursInDayNotWorked",` to `CALCULATED_FIELDS`, directly after `"payroll.employment.fte",`.

Replace the `Route` dataclass with:

```python
@dataclass(frozen=True)
class Route:
    kind: RouteKind
    table: str | None = None
    reason: str | None = None
    column: str | None = None
    wire: str | None = None
```

In `route_for`, replace everything from `if field.historical:` to the end of the function with:

```python
    if not field.historical:
        for prefix, reason in DERIVED_PREFIXES:
            if field.id.startswith(prefix):
                return Route("not_writable", reason=reason)
    if field.historical:
        if field.id in DATED_COLUMNS:
            table, column, wire = DATED_COLUMNS[field.id]
            return Route("dated", table=table, column=column, wire=wire)
        custom = CUSTOM_COLUMN.fullmatch(field.id)
        if custom:
            return Route(
                "dated",
                table=CUSTOM_COLUMN_TABLES[custom[1]],
                column=f"customColumns.{custom[2]}",
                wire="text",
            )
        if field.id in UNSUPPORTED_DATED:
            return Route("not_writable", reason=UNSUPPORTED_DATED[field.id])
        if field.id.startswith("address."):
            return Route("not_writable", reason="HiBob's API cannot change an address")
        return Route(
            "not_writable", reason="HiBob's API has no way to change this dated field"
        )
    return Route("field")
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest -q 2>&1 | tail -3`
Expected: all pass (including `test_real_metadata_fixture` against the captured tenant metadata).

- [ ] **Step 6: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add src/hibob_advanced_mcp/people_fields.py tests/people_data.py tests/test_people_fields.py
git commit -q -m "Route dated fields to the columns of their table

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Pure table logic

**Files:**
- Create: `src/hibob_advanced_mcp/employee_tables.py`
- Modify: `src/hibob_advanced_mcp/employee_rows.py` (`same_value`)
- Test: `tests/test_employee_tables.py` (new), `tests/test_employee_values.py`

**Interfaces:**
- Consumes: `employee_rows.same_value`.
- Produces (exact signatures):
  - `TableSpec(key: str, label: str, path: str, reason_column: str | None)`; `TABLES: dict[str, TableSpec]` with keys `"work"`, `"employment"`, `"salary"`; `TABLE_ORDER = ("work", "employment", "salary")`
  - `to_wire(wire: str, value: Any, label: str) -> Any`
  - `restricted_message(table: TableSpec, restricted: Any) -> str | None`
  - `split_rows(rows: list[dict[str, Any]], day: str, table: TableSpec) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]` returning `(base, same_day, later)`
  - `held_value(row: dict[str, Any] | None, column: str) -> Any`
  - `row_value(row: dict[str, Any] | None, column: str) -> Any`
  - `build_row(base: dict[str, Any] | None, changes: dict[str, Any], day: str, table: TableSpec, reason: str | None = None) -> dict[str, Any]`
  - `later_conflicts(later: list[dict[str, Any]], changes: dict[str, Any]) -> list[dict[str, Any]]`
  - `compare_row(body: dict[str, Any], row: dict[str, Any]) -> list[dict[str, Any]]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_employee_tables.py`:

```python
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
        "effectiveDate": "d",
        "fte": 100,
        "weeklyHours": 40,
        "customColumns": {"column_1": "A"},
        "column_1": "A",
    }
    read = _row("d", fte=0, weeklyHours=0, customColumns={"column_1": "B"})
    assert compare_row(sent, read) == [
        {"column": "customColumns.column_1", "sent": "A", "read": "B"}
    ]


def test_compare_row_compares_people_by_id_and_amounts_by_value() -> None:
    sent = {"reportsTo": {"id": "9"}, "base": {"value": 5000, "currency": "GBP"}}
    read = _row("d", reportsTo=MANAGER, base={"value": 5000.0, "currency": "GBP"})
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
```

Append these cases to the `test_same_value` parametrization in `tests/test_employee_values.py` (inside the existing list of `(sent, read, same)` tuples):

```python
        ({"id": "5"}, {"id": "5", "displayName": "Sam"}, True),
        ({"id": "5"}, {"id": "9", "displayName": "Sam"}, False),
        ({"id": "5"}, None, False),
        ({"a": 1, "b": {"c": 2}}, {"a": 1.0, "b": {"c": 2}, "x": 9}, True),
        ({"a": 1, "b": {"c": 2}}, {"a": 1, "b": {"c": 3}}, False),
        ({"value": 5, "currency": "GBP"}, {"value": 5, "currency": "gbp"}, True),
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_employee_tables.py tests/test_employee_values.py -q 2>&1 | tail -6`
Expected: `ModuleNotFoundError: No module named 'hibob_advanced_mcp.employee_tables'`.

- [ ] **Step 3: Implement `same_value`**

In `src/hibob_advanced_mcp/employee_rows.py`, replace the body of `same_value` (keep its signature and docstring) with:

```python
    if isinstance(sent, dict) and isinstance(read, dict):
        if "id" in sent:
            return normalize_id(sent["id"]) == normalize_id(read.get("id"))
        if "value" in sent:
            return normalize_id(sent["value"]) == normalize_id(read.get("value")) and (
                str(sent.get("currency", "")).upper()
                == str(read.get("currency", "")).upper()
            )
        return all(
            key in read and same_value(item, read[key]) for key, item in sent.items()
        )
    if isinstance(read, dict) and "id" in read and not isinstance(sent, dict):
        read = read["id"]
    if isinstance(sent, list) and isinstance(read, list):
        return sorted(normalize_id(v) for v in sent) == sorted(
            normalize_id(v) for v in read
        )
    if isinstance(sent, bool):
        return str(sent).lower() == str(read).strip().lower()
    if sent is None or read is None:
        return sent is read
    return normalize_id(sent) == normalize_id(read)
```

Also extend the docstring's last line to: `... a person read back as {"id", ...}, numbers as floats, lists reordered, dicts compared by what was sent.`

- [ ] **Step 4: Implement `employee_tables.py`**

Create `src/hibob_advanced_mcp/employee_tables.py`:

```python
"""Rows of HiBob's effective-dated tables: work, employment and salary.

HiBob replaces a table row wholesale: a column left out of a write is stored
empty. So a change to one column is written as a new row that copies the
row in force before its date and lays the change over it. These pure
functions choose that row, build the new one, and compare what HiBob holds
afterwards with what was sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .employee_rows import same_value

CUSTOM_PREFIX = "customColumns."


@dataclass(frozen=True)
class TableSpec:
    key: str
    label: str
    path: str
    reason_column: str | None


TABLES = {
    "work": TableSpec("work", "work", "work", "reason"),
    "employment": TableSpec("employment", "employment", "employment", "reason"),
    "salary": TableSpec("salary", "salary", "salaries", None),
}
TABLE_ORDER = ("work", "employment", "salary")

# Read-only or bookkeeping keys of a row, which a new row never carries.
BOOKKEEPING_KEYS = frozenset(
    {
        "id",
        "effectiveDate",
        "endEffectiveDate",
        "activeEffectiveDate",
        "isCurrent",
        "canBeDeleted",
        "change",
        "creationDate",
        "CreationDate",
        "modificationDate",
        "workChangeType",
        "humanReadable",
        "changedBy",
    }
)
# A label HiBob shows beside the ID it is written as: only the ID is sent.
LABEL_BESIDE_ID = {
    "site": "siteId",
    "calendarName": "calendarId",
    "standardWorkingPattern": "standardWorkingPatternId",
}
# Columns HiBob works out from others: copied as read, never compared after.
DERIVED_COLUMNS = frozenset(
    {
        "fte",
        "weeklyHours",
        "hoursInDayNotWorked",
        "actualWorkingPattern",
        "siteWorkingPattern",
        *LABEL_BESIDE_ID,
    }
)
NOT_COMPARED = DERIVED_COLUMNS | {"effectiveDate", "reason"}
# The column holding the name of an ID column, for showing people.
DISPLAY_COLUMN = {"siteId": "site", "calendarId": "calendarName"}


def to_wire(wire: str, value: Any, label: str) -> Any:
    """``value`` as a table's write body takes it."""
    if wire == "int":
        text = str(value).strip()
        if not text.isdigit():
            raise ValueError(f"{label} needs a numeric list item ID, not {value!r}.")
        return int(text)
    if wire == "employee":
        return {"id": str(value)}
    return value


def restricted_message(table: TableSpec, restricted: Any) -> str | None:
    """Why a table read cannot be trusted, or None.

    HiBob lists under restricted_columns the columns the service user may not
    see, or may not see the history of.
    """
    if not isinstance(restricted, dict):
        return None
    needs = {"no_view_permission": "View", "no_view_history_permission": "View history"}
    parts = [
        f"{needs.get(kind, kind)} on {', '.join(str(c) for c in columns)}"
        for kind, columns in restricted.items()
        if isinstance(columns, list) and columns
    ]
    if not parts:
        return None
    return (
        f"HiBob hides part of the {table.label} table from the service user, and a "
        "new row built from a partial read would blank what it cannot see. Grant "
        "People's data > People's fields: " + "; ".join(parts) + "."
    )


def split_rows(
    rows: list[dict[str, Any]], day: str, table: TableSpec
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    """(base, same_day, later) for a new row dated ``day``.

    The base is the latest row dated before it; the row marked current is not
    used, because a row may be added in the past or the future. ``later`` is
    every row dated after it, earliest first.
    """
    dated = [row for row in rows if isinstance(row.get("effectiveDate"), str)]
    same = next((row for row in dated if row["effectiveDate"] == day), None)
    earlier = [row for row in dated if row["effectiveDate"] < day]
    later = sorted(
        (row for row in dated if row["effectiveDate"] > day),
        key=lambda row: row["effectiveDate"],
    )
    base = None
    if earlier:
        newest = max(row["effectiveDate"] for row in earlier)
        tied = [row for row in earlier if row["effectiveDate"] == newest]
        if len(tied) > 1:
            raise ValueError(
                f"Two {table.label} rows are dated {newest}, so there is no single "
                "row to copy."
            )
        base = tied[0]
    return base, same, later


def held_value(row: dict[str, Any] | None, column: str) -> Any:
    """What a row stores in a column, as HiBob returns it."""
    if not row:
        return None
    if column.startswith(CUSTOM_PREFIX):
        return (row.get("customColumns") or {}).get(column[len(CUSTOM_PREFIX) :])
    return row.get(column)


def row_value(row: dict[str, Any] | None, column: str) -> Any:
    """What a row holds in a column, as a person would read it."""
    if not row:
        return None
    readable = row.get("humanReadable")
    if isinstance(readable, dict) and readable.get(column) not in (None, ""):
        return readable[column]
    value = held_value(row, column)
    if isinstance(value, dict) and value.get("displayName"):
        return value["displayName"]
    name_column = DISPLAY_COLUMN.get(column)
    if name_column and row.get(name_column):
        return row[name_column]
    return value


def build_row(
    base: dict[str, Any] | None,
    changes: dict[str, Any],
    day: str,
    table: TableSpec,
    reason: str | None = None,
) -> dict[str, Any]:
    """The body of a new row: the base row's columns with ``changes`` laid over.

    Null columns are left out (HiBob stores an omitted column as empty, which
    is the same). Custom columns are sent in both shapes HiBob is known to
    take: nested, as its reference documents, and as top-level keys, as
    justparent's integration sends them.
    """
    body: dict[str, Any] = {}
    custom: dict[str, Any] = {}
    for key, value in (base or {}).items():
        if key in BOOKKEEPING_KEYS or value is None or value == {}:
            continue
        if key == "customColumns":
            if isinstance(value, dict):
                custom.update(value)
        elif key == "reportsTo" and isinstance(value, dict) and "id" in value:
            body[key] = {"id": str(value["id"])}
        else:
            body[key] = value
    for column, value in changes.items():
        if column.startswith(CUSTOM_PREFIX):
            custom[column[len(CUSTOM_PREFIX) :]] = value
        else:
            body[column] = value
    for label, identifier in LABEL_BESIDE_ID.items():
        if identifier in body:
            body.pop(label, None)
    if custom:
        body["customColumns"] = custom
        for column, value in custom.items():
            body.setdefault(column, value)
    body["effectiveDate"] = day
    if reason and table.reason_column:
        body[table.reason_column] = reason
    return body


def later_conflicts(
    later: list[dict[str, Any]], changes: dict[str, Any]
) -> list[dict[str, Any]]:
    """The later rows that still hold a different value in a changed column."""
    found: list[dict[str, Any]] = []
    for row in later:
        differing = {
            column: row_value(row, column)
            for column, value in changes.items()
            if not same_value(value, held_value(row, column))
        }
        if differing:
            found.append({"effectiveDate": row["effectiveDate"], "columns": differing})
    return found


def compare_row(body: dict[str, Any], row: dict[str, Any]) -> list[dict[str, Any]]:
    """The columns of ``row`` (as HiBob holds it) that differ from ``body``."""
    custom = body.get("customColumns")
    custom = custom if isinstance(custom, dict) else {}
    problems: list[dict[str, Any]] = []
    for column, value in body.items():
        if column in NOT_COMPARED or column in custom:
            continue
        if column == "customColumns":
            for name, sent in custom.items():
                held = (row.get("customColumns") or {}).get(name)
                if not same_value(sent, held):
                    problems.append(
                        {
                            "column": f"{CUSTOM_PREFIX}{name}",
                            "sent": sent,
                            "read": held,
                        }
                    )
            continue
        held = row.get(column)
        if not same_value(value, held):
            problems.append({"column": column, "sent": value, "read": held})
    return problems
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest -q 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add src/hibob_advanced_mcp/employee_tables.py src/hibob_advanced_mcp/employee_rows.py tests/test_employee_tables.py tests/test_employee_values.py
git commit -q -m "Add the pure logic for adding rows to dated tables

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Plan dated changes

Dated changes are resolved to what HiBob takes and, without an effective date, come back as one question. Writing them follows in Task 4, so until then a dated change with a date is refused.

**Files:**
- Modify: `src/hibob_advanced_mcp/employee_values.py`, `src/hibob_advanced_mcp/employee_updates.py`
- Modify: `tests/people_data.py` (`LISTS`)
- Test: `tests/test_employee_values.py`, `tests/test_tools_employee_updates.py`

**Interfaces:**
- Consumes: `Route.column`, `Route.wire` (Task 1); `employee_tables.to_wire` (Task 2).
- Produces: `coerce_value(field, value, *, bare_amount_ok=False)`; a bare amount comes back as `{"value": n, "currency": None}` when `bare_amount_ok`; `_plan` returns `Change`s for dated fields whose `value` is already in wire form (`int`, `{"id": ...}` or an amount).

- [ ] **Step 1: Add lists to the test data**

In `tests/people_data.py`, add to the `LISTS` dict:

```python
    "title": {
        "name": "title",
        "values": [
            {"id": "101", "name": "Analyst", "value": "Analyst"},
            {"id": "102", "name": "Head of Data", "value": "Head of Data"},
            {"id": "103", "name": "Director", "value": "Director"},
        ],
    },
    "site": {
        "name": "site",
        "values": [
            {"id": 2606110, "name": "London (Demo)", "value": "London (Demo)"},
            {"id": 2606111, "name": "New York (Demo)", "value": "New York (Demo)"},
            {"id": 2606112, "name": "Madrid (Demo)", "value": "Madrid (Demo)"},
        ],
    },
    "employmentstatus": {
        "name": "employmentstatus",
        "values": [
            {"id": "Full time", "name": "Full time", "value": "Full time"},
            {"id": "Part time", "name": "Part time", "value": "Part time"},
        ],
    },
    "payrollEmploymentType": {
        "name": "payrollEmploymentType",
        "values": [
            {"id": "Permanent", "name": "Permanent", "value": "Permanent"},
            {"id": "Temporary", "name": "Temporary", "value": "Temporary"},
        ],
    },
    "calendar": {
        "name": "calendar",
        "values": [
            {"id": 2657450, "name": "Canada bank holidays", "value": "Canada bank holidays"},
            {"id": 2657449, "name": "Hong Kong bank holidays", "value": "Hong Kong bank holidays"},
        ],
    },
    "payPeriod": {
        "name": "payPeriod",
        "values": [
            {"id": "Annual", "name": "Annual", "value": "Annual"},
            {"id": "Monthly", "name": "Monthly", "value": "Monthly"},
        ],
    },
    "payFrequency": {
        "name": "payFrequency",
        "values": [
            {"id": "Weekly", "name": "Weekly", "value": "Weekly"},
            {"id": "Monthly", "name": "Monthly", "value": "Monthly"},
        ],
    },
```

Also add a person with a unique name to `DIRECTORY["employees"]`:

```python
        {"id": "79", "displayName": "Priya Patel", "email": "priya@x.com"},
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_employee_values.py`:

```python
def test_a_bare_amount_is_accepted_when_the_row_supplies_the_currency() -> None:
    field = BY_ID["payroll.salary.payment"]
    assert coerce_value(field, 60000, bare_amount_ok=True) == {
        "value": 60000,
        "currency": None,
    }
    assert coerce_value(field, {"value": "5", "currency": "gbp"}, bare_amount_ok=True) == {
        "value": 5,
        "currency": "GBP",
    }
    with pytest.raises(NeedsInput):
        coerce_value(field, 60000)
```

In `tests/test_tools_employee_updates.py`, replace the parametrized case `({"Job title": "Head of Data"}, "not supported yet"),` in `test_refusals_write_nothing` by removing that line, and append:

```python
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
```

(Add `import pytest` and `from hibob_advanced_mcp.employee_values import NeedsInput, coerce_value` and `from hibob_advanced_mcp.people_fields import normalize_people_fields` plus `from people_data import FIELDS` to the imports of `tests/test_employee_values.py` only if not already imported; `BY_ID` already exists there.)

- [ ] **Step 3: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_employee_values.py tests/test_tools_employee_updates.py -q 2>&1 | tail -10`
Expected: failures: `coerce_value` rejects `bare_amount_ok`; dated changes are still refused as "not supported yet" instead of asking for a date; no "given twice".

- [ ] **Step 4: Implement**

In `src/hibob_advanced_mcp/employee_values.py`:

- Change `def _currency(field: PeopleField, value: Any) -> dict[str, Any]:` to `def _currency(field: PeopleField, value: Any, bare_ok: bool) -> dict[str, Any]:`.
- In `_currency`, directly after `amount = _number(field, value)` add:

```python
        if bare_ok:
            return {"value": amount, "currency": None}
```

- Change `def coerce_value(field: PeopleField, value: Any) -> Any:` to `def coerce_value(field: PeopleField, value: Any, *, bare_amount_ok: bool = False) -> Any:` and `return _currency(field, value)` to `return _currency(field, value, bare_amount_ok)`.

In `src/hibob_advanced_mcp/employee_updates.py`:

- Add `from .employee_tables import to_wire` with the other imports.
- Change `_resolve_value`'s signature to `async def _resolve_value(api, cache, target, given, *, bare_amount_ok: bool = False) -> Any:` (keep the annotations as they are) and its last line `return coerce_value(target, given)` to `return coerce_value(target, given, bare_amount_ok=bare_amount_ok)`.
- In `_plan`, add `seen: dict[str, str] = {}` directly after `fields = await people_fields(api, cache)`.
- In `_plan`, directly after the `if len(matches) != 1:` block (after its `continue`) and before `target = matches[0]`'s route lookup, so the sequence reads `target = matches[0]`, then:

```python
        if target.id in seen:
            plan.problems.append(
                f"{target.qualified_label} is given twice, as {seen[target.id]!r} "
                f"and {key!r}."
            )
            continue
        seen[target.id] = key
```

- In `_plan`, delete the whole `if route.kind == "dated":` block (the one that appends "adding rows to it is not supported yet").
- In `_plan`, replace the `try:` block that resolves the value with:

```python
        try:
            value = await _resolve_value(
                api, cache, target, given, bare_amount_ok=route.wire == "amount"
            )
            if route.kind == "dated":
                value = to_wire(route.wire or "text", value, target.qualified_label)
        except NeedsInput as need:
            plan.questions.append({"key": key, **need.question})
            continue
        except ValueError as exc:
            plan.problems.append(str(exc))
            continue
```

- Add this function above `_apply` (or above `_check_schedule`):

```python
def _date_question(plan: Plan, dated: list[Change]) -> dict[str, Any]:
    """The one question that covers every change HiBob keeps dated rows for."""
    who = (plan.employee or {}).get("name") or "the employee"
    names = ", ".join(change.field.label for change in dated)
    return {
        "argument": "effective_date",
        "question": f"From what date should {names} change for {who}?",
        "applies_to": [change.field.qualified_label for change in dated],
    }
```

- In `hibob_update_employee`, replace the block from `if plan.questions or plan.employee is None:` to `return _dump(await _apply(api, plan, reason, sleep))` with:

```python
            dated = [c for c in plan.changes if c.route.kind == "dated"]
            if dated and day is None:
                plan.questions.append(_date_question(plan, dated))
            if plan.questions or plan.employee is None:
                return _dump(
                    {
                        "status": "needs_input",
                        "employee": plan.employee,
                        "questions": plan.questions,
                    }
                )
            _check_schedule(day, [c for c in plan.changes if c.route.kind != "dated"])
            if dated:
                raise ValueError(
                    "Adding rows to HiBob's dated tables is not supported yet. "
                    f"{NOTHING_WRITTEN}"
                )
            return _dump(await _apply(api, plan, reason, sleep))
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest -q 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add src/hibob_advanced_mcp/employee_values.py src/hibob_advanced_mcp/employee_updates.py tests/people_data.py tests/test_employee_values.py tests/test_tools_employee_updates.py
git commit -q -m "Plan dated changes: wire values, one date question, no duplicate fields

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Read the tables, write the rows, read them back

**Files:**
- Modify: `tests/people_data.py` (tables on `FakePeople`)
- Modify: `src/hibob_advanced_mcp/employee_updates.py`, `src/hibob_advanced_mcp/errors.py`, `README.md`
- Test: `tests/test_tools_employee_tables.py` (new), `tests/test_errors.py`, `tests/test_tools_employee_updates.py` (remove the Task 3 stand-in)

**Interfaces:**
- Consumes: everything from Tasks 1–3; `people_api.read_table(api, employee_id, path) -> {"rows": [...newest first], "restricted_columns": {...}}`.
- Produces: `hibob_update_employee(..., allow_later_rows: bool = False)`; result keys `rows_added: [{"table", "effective_date", "sent"}]` and applied `via` of `"<table> row from <date>"`; questions with `argument` `"allow_later_rows"`.

- [ ] **Step 1: Give the fake HiBob tables**

In `tests/people_data.py` add `import datetime as dt` to the imports. Above `class FakePeople` add:

```python
PATTERN = {
    "workingPatternType": "hourly",
    "days": {
        "monday": 8,
        "tuesday": 8,
        "wednesday": 8,
        "thursday": 8,
        "friday": 8,
        "saturday": 0,
        "sunday": 0,
    },
    "hoursPerDay": 8,
    "workingPatternId": 0,
}
SITES = {
    2606110: "London (Demo)",
    2606111: "New York (Demo)",
    2606112: "Madrid (Demo)",
}
# Every column a row has; HiBob stores any a write leaves out as null.
TABLE_COLUMNS = {
    "work": ("title", "department", "site", "siteId", "reportsTo", "workChangeType"),
    "employment": (
        "contract",
        "type",
        "salaryPayType",
        "flsaCode",
        "calendarId",
        "calendarName",
        "personalWorkingPatternType",
        "workingPattern",
        "standardWorkingPattern",
        "standardWorkingPatternId",
        "siteWorkingPattern",
        "actualWorkingPattern",
        "hoursInDayNotWorked",
        "fte",
        "weeklyHours",
    ),
    "salaries": ("base", "payPeriod", "payFrequency"),
}


def row_header(row_id: int, day: str, reason: str | None = None) -> dict[str, Any]:
    return {
        "id": row_id,
        "effectiveDate": day,
        "endEffectiveDate": None,
        "isCurrent": False,
        "canBeDeleted": True,
        "change": {"reason": reason, "changedBy": None, "changedById": "1"},
        "creationDate": None,
        "modificationDate": day,
        "activeEffectiveDate": day,
    }


def _renumber(rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda row: row["effectiveDate"])
    today = dt.date.today().isoformat()
    current = None
    for row in rows:
        row["isCurrent"] = False
        if row["effectiveDate"] <= today:
            current = row
    if current is not None:
        current["isCurrent"] = True


def _start_tables() -> dict[str, list[dict[str, Any]]]:
    work = {
        **row_header(1, "2024-03-01"),
        **{column: None for column in TABLE_COLUMNS["work"]},
        "title": "101",
        "department": "201",
        "site": SITES[2606110],
        "siteId": 2606110,
        "reportsTo": {
            "id": MANAGER_ID,
            "firstName": "Sam",
            "surname": "Jones",
            "email": "sam@x.com",
            "displayName": "Sam Jones",
        },
        "workChangeType": "New Employee",
        "customColumns": {},
    }
    employment = {
        **row_header(1, "2024-03-01"),
        **{column: None for column in TABLE_COLUMNS["employment"]},
        "contract": "Full time",
        "siteWorkingPattern": PATTERN,
        "actualWorkingPattern": PATTERN,
        "hoursInDayNotWorked": 8,
        "fte": 100,
        "weeklyHours": 40,
        "customColumns": {},
    }
    tables: dict[str, list[dict[str, Any]]] = {
        "work": [work],
        "employment": [employment],
        "salaries": [],
    }
    for rows in tables.values():
        _renumber(rows)
    return tables
```

In `FakePeople.__init__`, after the `self.email = ...` statement, add:

```python
        self.tables = _start_tables()
        self.restricted: dict[str, dict[str, Any]] = {}
        self.row_status: dict[str, int] = {}
        self.drop_on_write: dict[str, set[str]] = {}
        self.posted: list[tuple[str, dict[str, Any]]] = []
        self.reads: list[str] = []
        for path in self.tables:
            mock_api.get(f"/people/{EMPLOYEE_ID}/{path}").mock(
                side_effect=self._table_read(path)
            )
            mock_api.post(f"/people/{EMPLOYEE_ID}/{path}").mock(
                side_effect=self._table_write(path)
            )
```

Add these methods to `FakePeople`:

```python
    def add_row(self, path: str, day: str, **columns: Any) -> dict[str, Any]:
        rows = self.tables[path]
        row = {
            **row_header(max((r["id"] for r in rows), default=0) + 1, day),
            **{column: None for column in TABLE_COLUMNS[path]},
            "customColumns": {},
            **columns,
        }
        rows.append(row)
        _renumber(rows)
        return row

    def _table_read(self, path: str):
        def handler(request: httpx.Request) -> httpx.Response:
            self.reads.append(path)
            return httpx.Response(
                200,
                json={
                    "values": self.tables[path],
                    "restricted_columns": self.restricted.get(path, {}),
                },
            )

        return handler

    def _table_write(self, path: str):
        def handler(request: httpx.Request) -> httpx.Response:
            body = jsonlib.loads(request.content)
            self.writes.append(f"row:{path}")
            self.posted.append((path, body))
            status = self.row_status.get(path, 200)
            if status != 200:
                return httpx.Response(
                    status,
                    json={
                        "key": "exception.history.duplicated.bulk",
                        "error": "Duplicate effective date for work, please "
                        "update the effective date.",
                    },
                )
            columns = TABLE_COLUMNS[path]
            stored = {column: None for column in columns}
            stored.update({k: v for k, v in body.items() if k in columns})
            custom = dict(body.get("customColumns") or {})
            custom.update({k: v for k, v in body.items() if k.startswith("column_")})
            stored["customColumns"] = custom
            if stored.get("siteId") is not None and not stored.get("site"):
                stored["site"] = SITES.get(stored["siteId"])
            for column in self.drop_on_write.get(path, ()):
                stored[column] = None
            rows = self.tables[path]
            header = row_header(
                max((r["id"] for r in rows), default=0) + 1,
                body["effectiveDate"],
                body.get("reason"),
            )
            rows.append({**header, **stored})
            _renumber(rows)
            return httpx.Response(200)

        return handler
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_tools_employee_tables.py`:

```python
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
            changes={"Contract": "part time"},
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


async def test_a_first_salary_row_needs_the_amount_and_the_pay_period(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={
                "Base salary": {"value": 50000, "currency": "gbp"},
                "Pay period": "annual",
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
            changes={"Base salary": 50000, "Pay period": "Annual"},
            effective_date="2026-11-01",
        )
    )
    assert result["status"] == "needs_input"
    [question] = result["questions"]
    assert "currency" in question["question"]
    assert fake.writes == []


async def test_a_first_salary_row_without_a_pay_period_asks_for_it(
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
    assert "pay period" in result["questions"][0]["question"]
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
                "Pay period": "Annual",
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
            changes={"Job title": "Head of Data", "Contract": "Part time"},
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
```

Append to `tests/test_errors.py`:

```python
def test_400_for_a_duplicate_effective_date_says_rows_are_only_added() -> None:
    body = {
        "key": "exception.history.duplicated.bulk",
        "error": "Duplicate effective date for work, please update the effective date.",
    }
    with pytest.raises(HiBobApiError) as excinfo:
        raise_for_hibob_error(_response(400, body, url=f"{PEOPLE_URL}/work"))
    message = str(excinfo.value)
    assert "Duplicate effective date for work" in message
    assert "only adds rows" in message
    assert "hibob_list_employee_fields" not in message
```

In `tests/test_tools_employee_updates.py`, delete `test_a_dated_change_with_a_date_is_not_written_until_rows_are_supported` (added in Task 3).

- [ ] **Step 3: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_tools_employee_tables.py tests/test_errors.py -q 2>&1 | tail -12`
Expected: failures: the tool has no `allow_later_rows`, dated changes are refused as "not supported yet", and there is no "only adds rows" hint.

- [ ] **Step 4: Implement the error hint**

In `src/hibob_advanced_mcp/errors.py`, replace the `elif status == 400 and "/people/" in path:` branch with:

```python
    elif status == 400 and "/people/" in path:
        duplicate = bool(detail) and "duplicate effective date" in str(detail).lower()
        message = (
            "HiBob rejected the employee change (400)"
            + (f": {detail.rstrip('.')}." if detail else ".")
            + (
                " A row already exists on that date; this server only adds rows, "
                "so use a different effective date."
                if duplicate
                else " Check field IDs and values with hibob_list_employee_fields."
            )
        )
```

- [ ] **Step 5: Implement row planning, writing and read-back**

In `src/hibob_advanced_mcp/employee_updates.py`:

Add to the imports:

```python
from .employee_tables import (
    TABLE_ORDER,
    TABLES,
    TableSpec,
    build_row,
    compare_row,
    held_value,
    later_conflicts,
    restricted_message,
    row_value,
    split_rows,
    to_wire,
)
```
and change `from .people_api import named_list, people_fields, read_employee` to also import `read_table`.

Add below `START_DATE_PATH`:

```python
ROW_PATH = "/people/{employee_id}/{table}"
# A table with no earlier salary row needs these to start one.
FIRST_SALARY_COLUMNS = (
    ("base", "the amount with its currency"),
    ("payPeriod", "the pay period, for example Annual"),
)
```

Below the `Plan` dataclass add:

```python
@dataclass
class RowPlan:
    table: TableSpec
    day: str
    body: dict[str, Any]
    changes: list[Change]
    before: dict[str, Any]


@dataclass
class Write:
    name: str
    kind: str
    changes: list[Change]
    row: RowPlan | None = None
```

Replace everything from `def _applied(` up to (not including) `def register_update_tools(` with this block:

```python
def _applied(change: Change, before: dict[str, Any], via: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "field": change.field.qualified_label,
        "id": change.field.id,
        "to": change.given,
        "via": via,
    }
    if change.value != change.given:
        entry["sent"] = change.value
    if change.field.id in before:
        entry["from"] = before[change.field.id]
    return entry


async def _current(
    api: HiBobClient, employee_id: str, changes: list[Change]
) -> dict[str, Any]:
    """What each field holds before the change, as a label where HiBob has one."""
    try:
        record = await read_employee(
            api, employee_id, [c.field.id for c in changes], human_readable=True
        )
    except Exception:
        return {}
    before: dict[str, Any] = {}
    for change in changes:
        value, display = read_field(record, change.field.id, change.field.json_path)
        before[change.field.id] = display if display not in (None, "") else value
    return before


def _fill_amounts(
    who: str, table: TableSpec, group: list[Change], base: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Give each bare amount the currency of the row being copied, or ask."""
    asks: list[dict[str, Any]] = []
    for change in group:
        amount = change.value
        if not (
            isinstance(amount, dict)
            and "currency" in amount
            and amount["currency"] is None
        ):
            continue
        currency = ((base or {}).get("base") or {}).get("currency")
        if currency:
            change.value = {"value": amount["value"], "currency": currency}
            continue
        asks.append(
            {
                "argument": "changes",
                "key": change.key,
                "question": (
                    f"{who} has no earlier {table.label} row to take a currency "
                    f"from. In what currency is {change.field.label} "
                    f"{amount['value']}? Give it as "
                    f'{{"value": {amount["value"]}, "currency": "GBP"}}.'
                ),
            }
        )
    return asks


def _first_salary_question(
    who: str, day: str, columns: set[str], labels: list[str]
) -> list[dict[str, Any]]:
    missing = [what for column, what in FIRST_SALARY_COLUMNS if column not in columns]
    if not missing:
        return []
    return [
        {
            "argument": "changes",
            "question": (
                f"{who} has no salary row before {day}, so a new one needs "
                f"{' and '.join(missing)}. Add them to changes."
            ),
            "applies_to": labels,
        }
    ]


def _later_question(
    who: str, table: TableSpec, conflicts: list[dict[str, Any]], labels: list[str]
) -> dict[str, Any]:
    first = conflicts[0]
    held = ", ".join(f"{column} {value!r}" for column, value in first["columns"].items())
    return {
        "argument": "allow_later_rows",
        "question": (
            f"A {table.label} row from {first['effectiveDate']} still has {held}, so "
            f"this change would only last until then. Go ahead anyway?"
        ),
        "applies_to": labels,
        "later_rows": conflicts,
    }


async def _prepare_rows(
    api: HiBobClient,
    plan: Plan,
    day: str,
    reason: str | None,
    allow_later_rows: bool,
) -> tuple[list[RowPlan], list[dict[str, Any]], list[str]]:
    """Read each table a change lands in and build the rows to add.

    Reads only. Anything an answer cannot fix is raised as a ValueError;
    questions and warnings are returned.
    """
    assert plan.employee is not None
    who = plan.employee.get("name") or "the employee"
    rows: list[RowPlan] = []
    questions: list[dict[str, Any]] = []
    warnings: list[str] = []
    for key in TABLE_ORDER:
        group = [c for c in plan.changes if c.route.table == key and c.route.column]
        if not group:
            continue
        table = TABLES[key]
        data = await read_table(api, plan.employee["id"], table.path)
        hidden = restricted_message(table, data["restricted_columns"])
        if hidden:
            raise ValueError(f"{hidden} {NOTHING_WRITTEN}")
        base, same_day, later = split_rows(data["rows"], day, table)
        if same_day is not None:
            raise ValueError(
                f"{who} already has a {table.label} row dated {day}. This tool only "
                f"adds rows, so choose a different effective date. {NOTHING_WRITTEN}"
            )
        if base is None and key != "salary":
            raise ValueError(
                f"{who} has no {table.label} row before {day}, so there is nothing "
                f"to carry forward. {NOTHING_WRITTEN}"
            )
        labels = [c.field.qualified_label for c in group]
        asks = _fill_amounts(who, table, group, base)
        if base is None:
            asks += _first_salary_question(
                who, day, {str(c.route.column) for c in group}, labels
            )
        if asks:
            questions.extend(asks)
            continue
        values = {str(c.route.column): c.value for c in group}
        if base is not None and all(
            same_value(value, held_value(base, column))
            for column, value in values.items()
        ):
            warnings.append(
                f"{who}'s {', '.join(c.field.label for c in group)} already hold "
                f"these values in the {table.label} row dated "
                f"{base['effectiveDate']}, so no {table.label} row was added."
            )
            continue
        conflicts = later_conflicts(later, values)
        if conflicts and not allow_later_rows:
            questions.append(_later_question(who, table, conflicts, labels))
            continue
        if reason and table.reason_column is None:
            warnings.append(
                f"HiBob's {table.label} table has no reason column, so the reason "
                f"was not recorded on the {table.label} row."
            )
        rows.append(
            RowPlan(
                table,
                day,
                build_row(base, values, day, table, reason),
                group,
                {c.field.id: row_value(base, str(c.route.column)) for c in group},
            )
        )
    return rows, questions, warnings


async def _send(
    api: HiBobClient, employee_id: str, write: Write, reason: str | None
) -> bool:
    """Send one write; False when HiBob reports it changed nothing (304)."""
    path_id = quote(employee_id, safe="")
    if write.row is not None:
        path = ROW_PATH.format(employee_id=path_id, table=write.row.table.path)
        await api.post(path, write.row.body)
        return True
    group = write.changes
    if write.kind == "field":
        body = put_body({c.field.json_path: c.value for c in group})
        response = await api.request_response(
            "PUT", PUT_PATH.format(employee_id=path_id), json=body
        )
        return response.status_code != 304
    if write.kind == "start_date":
        start: dict[str, Any] = {"startDate": group[0].value}
        if reason:
            start["reason"] = reason
        await api.post(START_DATE_PATH.format(employee_id=path_id), start)
        return True
    response = await api.request_response(
        "PUT", EMAIL_PATH.format(employee_id=path_id), json={"email": group[0].value}
    )
    return response.status_code != 304


def _note(categories: list[str]) -> str:
    return (
        "HiBob may still be applying these (its reads can lag writes by up to 20 "
        "seconds), or it ignored them because the service user cannot edit them"
        + (
            f" (People's data > People's fields: Edit on {', '.join(categories)})"
            if categories
            else ""
        )
        + ". A column HiBob dropped from a new row is empty in it. Check again with "
        "hibob_get_employee."
    )


async def _confirm(
    api: HiBobClient,
    employee_id: str,
    written: list[Change],
    result: dict[str, Any],
    sleep: SleepFn,
) -> None:
    """Read the changes back, re-reading for a while before calling one lost."""
    pending = list(written)
    seen: dict[str, Any] = {}
    try:
        for delay in READ_BACK_DELAYS:
            if delay:
                await sleep(delay)
            record = await read_employee(
                api, employee_id, [c.field.id for c in pending]
            )
            still: list[Change] = []
            for change in pending:
                value, _ = read_field(record, change.field.id, change.field.json_path)
                if not same_value(change.value, value):
                    seen[change.field.id] = value
                    still.append(change)
            pending = still
            if not pending:
                return
    except Exception as exc:
        result["verification_error"] = format_exception(exc)
        return
    categories = sorted({c.field.category for c in pending if c.field.category})
    result.setdefault("unconfirmed", []).extend(
        {
            "field": c.field.qualified_label,
            "sent": c.value,
            "read": seen.get(c.field.id),
        }
        for c in pending
    )
    result.setdefault("unconfirmed_note", _note(categories))


async def _confirm_row(
    api: HiBobClient,
    employee_id: str,
    row: RowPlan,
    result: dict[str, Any],
    sleep: SleepFn,
) -> None:
    """Read a new row back and compare every column sent."""
    problems: list[dict[str, Any]] = []
    try:
        for delay in READ_BACK_DELAYS:
            if delay:
                await sleep(delay)
            data = await read_table(api, employee_id, row.table.path)
            found = next(
                (r for r in data["rows"] if r.get("effectiveDate") == row.day), None
            )
            problems = (
                compare_row(row.body, found)
                if found is not None
                else [{"column": "(the new row)", "sent": row.day, "read": None}]
            )
            if not problems:
                return
    except Exception as exc:
        result["verification_error"] = format_exception(exc)
        return
    label = f"{row.table.label} row from {row.day}"
    categories = sorted({c.field.category for c in row.changes if c.field.category})
    result.setdefault("unconfirmed", []).extend(
        {"field": f"{label} > {p['column']}", "sent": p["sent"], "read": p["read"]}
        for p in problems
    )
    result.setdefault("unconfirmed_note", _note(categories))


def _explain(exc: Exception, group: list[Change]) -> Exception:
    """A permission refusal, naming the categories these fields are in."""
    if not isinstance(exc, HiBobApiError) or exc.status_code != 403:
        return exc
    categories = sorted({c.field.category for c in group if c.field.category})
    if not categories:
        return exc
    return HiBobApiError(
        f"{exc} Grant Edit on {', '.join(categories)}, the categories these "
        "fields are in.",
        status_code=exc.status_code,
        hibob_key=exc.hibob_key,
        hibob_error=exc.hibob_error,
    )


async def _apply(
    api: HiBobClient,
    plan: Plan,
    rows: list[RowPlan],
    reason: str | None,
    sleep: SleepFn,
    warnings: list[str],
) -> dict[str, Any]:
    assert plan.employee is not None
    employee_id = plan.employee["id"]
    others = [c for c in plan.changes if c.route.kind != "dated"]
    before = await _current(api, employee_id, others) if others else {}
    result: dict[str, Any] = {
        "status": "updated",
        "employee": plan.employee,
        "applied": [],
        "warnings": list(warnings),
    }
    writes = [
        Write(f"{row.table.label} row", "row", row.changes, row) for row in rows
    ]
    for kind in WRITE_ORDER:
        group = [c for c in others if c.route.kind == kind]
        if group:
            writes.append(Write(WRITE_NAMES[kind], kind, group))
    written: list[Change] = []
    added: list[RowPlan] = []
    for index, write in enumerate(writes):
        labels = ", ".join(c.field.qualified_label for c in write.changes)
        try:
            changed = await _send(api, employee_id, write, reason)
        except Exception as exc:
            explained = _explain(exc, write.changes)
            if index == 0:
                raise explained from exc
            result["status"] = "partial"
            result["failed"] = {
                "write": write.name,
                "fields": [c.field.qualified_label for c in write.changes],
                "error": format_exception(explained),
            }
            result["not_sent"] = [
                c.field.qualified_label
                for rest in writes[index + 1 :]
                for c in rest.changes
            ]
            break
        if not changed:
            result["warnings"].append(
                f"HiBob changed nothing for {labels}: they already had these values, "
                "or the service user cannot change them this way."
            )
            continue
        if write.row is not None:
            added.append(write.row)
            via = f"{write.row.table.label} row from {write.row.day}"
            result["applied"].extend(
                _applied(c, write.row.before, via) for c in write.changes
            )
            continue
        written.extend(write.changes)
        result["applied"].extend(
            _applied(c, before, VIA[write.kind]) for c in write.changes
        )
        if write.kind == "email":
            result["warnings"].append(
                "HiBob sends the employee a verification email at the new address."
            )
    if added:
        result["rows_added"] = [
            {"table": r.table.key, "effective_date": r.day, "sent": r.body}
            for r in added
        ]
    for row in added:
        await _confirm_row(api, employee_id, row, result, sleep)
    if written:
        await _confirm(api, employee_id, written, result, sleep)
    elif not added and result["status"] == "updated":
        result["status"] = "unchanged"
    if not result["warnings"]:
        del result["warnings"]
    return result


```

(The block ends with a blank line so `def register_update_tools(` follows after two blank lines. `to_wire` was imported in Task 3 and is still used by `_plan`; `held_value`, `row_value`, `later_conflicts` etc. are used above.)

In `hibob_update_employee`, add the parameter after `reason`:

```python
        allow_later_rows: Annotated[
            bool,
            Field(
                description=(
                    "True to go ahead when a later row in a dated table still holds "
                    "a different value in a column being changed, which means this "
                    "change only lasts until that row's date. Ask the user first."
                )
            ),
        ] = False,
```

Update the other parameter descriptions: `effective_date` → `"YYYY-MM-DD. Needed for job title, department, site, manager, employment and salary changes, which HiBob keeps as dated rows; the tool asks for it if missing. Plain fields change immediately and cannot be scheduled."`; `reason` → `"Why the change is made; recorded on new work and employment rows and with a start-date change (HiBob's salary table has no reason column)."`

Replace the tool's `Returns:` block and the paragraph after it in the docstring with:

```
        Returns:
            str: JSON {"status": "updated" | "unchanged" | "partial",
            "employee", "applied": [{"field", "id", "from"?, "to", "sent"?,
            "via"}], "rows_added"?: [{"table", "effective_date", "sent"}],
            "warnings"?, "unconfirmed"?: [{"field", "sent", "read"}],
            "unconfirmed_note"?, "failed"?, "not_sent"?}; or "needs_input" as
            above; or an error beginning "Error:" (nothing written).

        Job title, department, site, manager, employment terms and salary are
        HiBob dated rows, and HiBob replaces a row wholesale, so each is
        written as a new row dated effective_date that copies the row before
        that date with the change laid over it. The tool asks for a missing
        effective_date and never assumes today. It refuses, writing nothing,
        if the table cannot be read in full, a row already exists on that
        date (it never edits a row), or there is nothing earlier to copy
        (except a first salary row, which needs the amount with its currency
        and the pay period). A later row that still holds a different value
        comes back as a question; answer it with allow_later_rows=true.

        Writes go in this order, each sent once: new rows (work, employment,
        salary), plain fields (one PUT), start date, then work email (HiBob
        emails the employee to verify it). If one fails the rest are not sent
        ("partial"); nothing is rolled back. Each change is read back; HiBob
        silently skips fields the service user may not edit and its reads
        lag, so a change still not visible after about 10 seconds is listed
        under "unconfirmed".
```

Replace the body of the tool's `try:` block (from `if not changes:` to the `return`) with:

```python
            if not changes:
                raise ValueError("changes must name at least one field.")
            day = iso_date("effective_date", effective_date) if effective_date else None
            api = client_factory()
            plan = await _plan(api, cache, employee, changes)
            if plan.problems:
                raise ValueError(" ".join(plan.problems) + f" {NOTHING_WRITTEN}")
            dated = [c for c in plan.changes if c.route.kind == "dated"]
            if dated and day is None:
                plan.questions.append(_date_question(plan, dated))
            if plan.questions or plan.employee is None:
                return _dump(
                    {
                        "status": "needs_input",
                        "employee": plan.employee,
                        "questions": plan.questions,
                    }
                )
            _check_schedule(day, [c for c in plan.changes if c.route.kind != "dated"])
            rows: list[RowPlan] = []
            warnings: list[str] = []
            if dated and day is not None:
                rows, questions, warnings = await _prepare_rows(
                    api, plan, day, reason, allow_later_rows
                )
                if questions:
                    return _dump(
                        {
                            "status": "needs_input",
                            "employee": plan.employee,
                            "questions": questions,
                        }
                    )
            return _dump(await _apply(api, plan, rows, reason, sleep, warnings))
```

Update the module docstring's last sentence to: `Columns of effective-dated tables (work, employment, salary) are written as new rows that copy the row before their date.`

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/pytest -q 2>&1 | tail -4`
Expected: all pass. If an assertion about row order or `from` values fails, fix the code, not the test, unless the test contradicts the spec.

- [ ] **Step 7: Update the README**

In `README.md`, replace the sentence beginning `Fields HiBob keeps a dated history of (job title, department, site, manager, employment, salary) are refused for now;` through `adding dated rows is the next phase.` with:

```
Fields HiBob keeps a dated history of need an `effective_date`: job title, department, site, manager; employment contract, type, pay type, FLSA code and holiday calendar; salary amount, pay period and frequency. Without one the tool asks for it and never assumes today. Each such change adds a **new row** to HiBob's work, employment or salary table. HiBob replaces a row wholesale (a column left out is stored empty), so the new row copies every column of the row in force before its date and lays the change over it. Nothing is written if HiBob hides part of the table from the service user, the employee already has a row on that date (existing rows are never edited), or there is no earlier row to copy (a first salary row is the exception: it needs the amount with its currency and the pay period). A later row that still holds a different value comes back as a question; `allow_later_rows: true` goes ahead. A bare salary amount takes its currency from the row it copies, and a change that matches the row it would copy adds nothing. New rows carry HiBob's default change type; a reason is recorded on work and employment rows (the salary table has no reason column). Every new row is read back and compared column by column, and anything HiBob dropped is listed under `unconfirmed`. Working patterns cannot be changed yet, and the derived columns (FTE, weekly hours) are copied as they were.
```

Also in the sentence about derived fields, add `employment, salary and` hmm — leave it.

- [ ] **Step 8: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
uvx --no-cache --from . hibob-advanced-mcp --test 2>&1 | grep -E "Registered tools"
git add -A src tests README.md
git commit -q -m "Write dated rows: read, carry forward, add, read back

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
Expected: `Registered tools (32):`.

---

### Task 5: Check it live on the demo tenant (writes to the user's own record)

Writes go only to `david@harriethq.com`, dated 2030, so David's current values do not change. Rows cannot be removed by this tool and this task never deletes: it ends by listing the rows it added.

**Files:**
- Modify: `tests/fixtures/people/observations.md`, the spec (`docs/superpowers/specs/2026-10-07-employee-record-updates-design.md`)

- [ ] **Step 1: Recreate the harness (if the scratch copy is gone)**

The harness copies the package into the justparent `web` container, installs the MCP SDK into an isolated directory, and runs the tools in a child process that gets integration 3's credentials only through its environment. Write these three files to a scratch directory `$H`:

`$H/child.py`:

```python
import asyncio, json
from hibob_advanced_mcp.server import build_server

mcp = build_server(read_only=False)
CALLS = json.load(open("/tmp/hamcp/calls.json"))


async def call(name, args):
    result = await mcp.call_tool(name, args)
    content = result[0] if isinstance(result, tuple) else result
    return "".join(getattr(c, "text", "") for c in content)


async def main():
    out = []
    for name, args in CALLS:
        out.append({"tool": name, "args": args, "result": await call(name, args)})
    print("LIVE_JSON:" + json.dumps(out))


asyncio.run(main())
```

`$H/live_tools.py` (run through `manage.py shell`):

```python
import os, subprocess
from bots.models import Integration

i = Integration.objects.get(id=3)
env = {
    "PATH": os.environ["PATH"],
    "PYTHONPATH": "/tmp/hamcp:/tmp/hamcp_deps",
    "HIBOB_SERVICE_USER_ID": i.user_id,
    "HIBOB_SERVICE_USER_TOKEN": i.secret_key,
    "HIBOB_API_HOST": "api.hibob.com",
}
done = subprocess.run(
    ["python", "-s", "/tmp/hamcp/child.py"],
    env=env, cwd="/tmp", capture_output=True, text=True, timeout=600,
)
print(done.stdout)
print("CHILD_ERR:" + done.stderr[-3000:].replace("\n", "\\n"))
```

`$H/live.sh`:

```bash
#!/bin/bash
# usage: live.sh calls.json -> prints the LIVE_JSON results
set -e
H="$(cd "$(dirname "$0")" && pwd)"
REPO=/Users/david/Documents/workspace/hibob-advanced-mcp
cd /Users/david/Documents/workspace/justparent
docker compose exec -T web rm -rf /tmp/hamcp
docker compose exec -T web mkdir -p /tmp/hamcp
docker compose cp $REPO/src/hibob_advanced_mcp web:/tmp/hamcp/hibob_advanced_mcp
docker compose cp "$1" web:/tmp/hamcp/calls.json
docker compose cp $H/child.py web:/tmp/hamcp/child.py
docker compose exec -T web python justparent/manage.py shell < $H/live_tools.py 2>$H/live.err > $H/live.out
grep '^CHILD_ERR:' $H/live.out | sed 's/^CHILD_ERR://' > $H/child.err || true
grep '^LIVE_JSON:' $H/live.out | sed 's/^LIVE_JSON://'
```

If `/tmp/hamcp_deps` is missing in the container: `docker compose exec -T web pip install -q --target /tmp/hamcp_deps "mcp>=1.2,<2"` (from the justparent directory). Delete `/tmp/hamcp` and `/tmp/hamcp_deps` in the container when done.

- [ ] **Step 2: Ask for the date, then add the first work row**

Write `$H/calls_1.json` and run `$H/live.sh $H/calls_1.json`:

```json
[["hibob_update_employee", {"employee": "david@harriethq.com", "changes": {"Job title": "CEO", "Department": "Development", "Reports to": "Aisha Bello"}}],
 ["hibob_update_employee", {"employee": "david@harriethq.com", "changes": {"Job title": "CEO", "Department": "Development", "Reports to": "Aisha Bello"}, "effective_date": "2030-01-01", "reason": "API check"}]]
```

Expected: the first result is `needs_input` with one `effective_date` question naming "Job title, Department, Reports to". The second is `updated` with `rows_added[0].sent` equal to `{"effectiveDate": "2030-01-01", "title": "CEO", "department": "Development", "siteId": 2606110, "reportsTo": {"id": "3987598587553907167"}, "reason": "API check"}` and no `unconfirmed`. If HiBob answers 400, read its message: it decides whether `siteId` alone is enough (if it asks for `site`, send both) and record a ledger ruling.

- [ ] **Step 3: Check the base row is chosen by date**

`$H/calls_2.json`:

```json
[["hibob_update_employee", {"employee": "david@harriethq.com", "changes": {"Job title": "CTO"}, "effective_date": "2030-01-02"}],
 ["hibob_update_employee", {"employee": "david@harriethq.com", "changes": {"Job title": "Account Manager"}, "effective_date": "2029-12-31"}],
 ["hibob_update_employee", {"employee": "david@harriethq.com", "changes": {"Job title": "CFO"}, "effective_date": "2030-01-01"}]]
```

Expected: (1) `updated`; its `rows_added[0].sent` carries `department` "Development", the same `reportsTo` and `siteId` as the first row (copied from the 2030-01-01 row, not from David's current row, which has none of them). (2) `needs_input` with an `allow_later_rows` question naming 2030-01-01 and `title 'CEO'`. (3) `Error:` "already has a work row dated 2030-01-01". Nothing is written by (2) or (3).

- [ ] **Step 4: Employment and salary rows**

`$H/calls_3.json`:

```json
[["hibob_update_employee", {"employee": "david@harriethq.com", "changes": {"Employment type": "Permanent"}, "effective_date": "2030-01-01"}],
 ["hibob_update_employee", {"employee": "david@harriethq.com", "changes": {"Base salary": 100000}, "effective_date": "2030-01-01"}],
 ["hibob_update_employee", {"employee": "david@harriethq.com", "changes": {"Base salary": {"value": 100000, "currency": "USD"}, "Pay period": "Annual"}, "effective_date": "2030-01-01"}],
 ["hibob_update_employee", {"employee": "david@harriethq.com", "changes": {"Base salary": 110000}, "effective_date": "2030-01-02"}],
 ["hibob_update_employee", {"employee": "david@harriethq.com", "changes": {"Mobile phone": "1", "Job title": "CEO"}, "effective_date": "2030-03-01"}]]
```

Expected: (1) `updated`: HiBob accepts the copied derived columns (`fte`, `weeklyHours`, `actualWorkingPattern`, ...) and the read-back finds no difference. If it answers 400 naming a derived column, drop that column from `build_row`'s copy (add it to `BOOKKEEPING_KEYS`), re-run the unit tests, and record a ruling. (2) `needs_input` asking for the currency (and the pay period), nothing written. (3) `updated` (first salary row). (4) `updated`; `rows_added[0].sent.base.currency` is `USD` and `payPeriod` is `Annual`, copied from the first salary row. (5) `Error:` "cannot schedule" naming only the plain field.

- [ ] **Step 5: Read it all back and confirm David's current values did not change**

`$H/calls_4.json`:

```json
[["hibob_get_employee", {"employee": "david@harriethq.com", "fields": ["Job title", "Department", "Reports to", "Site"], "history": ["work", "employment", "salary"]}]]
```

Expected: current `Job title`, `Department` and `Reports to` are still empty and `Site` is "London (Demo)"; `history.work` has David's original row plus 2030-01-01 and 2030-01-02; `history.employment` has the original plus 2030-01-01 with `type` "Permanent" and `fte`, `weeklyHours` unchanged; `history.salary` has two rows (2030-01-01, 2030-01-02). Note each added row's `id` for the cleanup list.

- [ ] **Step 6: Record what was found**

Append to `tests/fixtures/people/observations.md` a "Dated rows (live)" section with: whether `siteId` alone was accepted; whether the copied derived employment columns were accepted; the salary and work row bodies HiBob stored; the read-back delay seen (add `0.7 s` if unchanged); and anything that differed from the plan, with the rulings made. In the spec's "Findings from the demo tenant" add a "Verified live" bullet list of the same. Update the memory note `hibob-employee-updates-built-blind.md`: phase 3 shipped, rows added to David's record (dates and IDs), and that cleanup is deliberately left to the user.

- [ ] **Step 7: Full check and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy && .venv/bin/pytest -q 2>&1 | tail -2
git add -A tests/fixtures/people/observations.md docs
git commit -q -m "Record the live behaviour of dated row writes

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 8: Tell the user what was left on the demo tenant**

Final message lists, for `david@harriethq.com`: the work rows (IDs, 2030-01-01 and 2030-01-02), the employment row (ID, 2030-01-01), the salary rows (IDs, 2030-01-01 and 2030-01-02), that none changes his current values, and that deleting them is left to the user in HiBob's UI (this plan and the tool never delete).
