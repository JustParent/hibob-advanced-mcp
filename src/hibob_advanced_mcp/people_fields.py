"""HiBob employee fields: what each one is, how to find it, how it is written.

GET /company/people/fields describes every employee field, for example
{"id": "work.title", "name": "Job title", "categoryDisplayName": "Work",
"type": "list", "typeData": {"listId": "title"}, "jsonPath": "work.title",
"historical": true}. A historical field is the current row of an
effective-dated table and cannot be written as a plain field; the metadata
does not say which table, so that is read from the ID's prefix. These pure
functions normalise the metadata, find the field a user means and say how a
change to it is written. They also read a value back out of a people record,
which HiBob has returned both keyed by slash path and nested by category.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from .forms import rank_matches

ROOT_PREFIX = "root."
EMAIL_FIELD = "root.email"
START_DATE_FIELD = "work.startDate"
MAX_CANDIDATES = 5
# Columns of the effective-dated tables (work, employment, salary, address) that
# a change can be written to: field ID -> (table, column in the table's write
# body, how the value is sent).
DATED_COLUMNS = {
    "work.title": ("work", "title", "text"),
    "work.department": ("work", "department", "text"),
    "work.siteId": ("work", "siteId", "int"),
    "work.reportsTo": ("work", "reportsTo", "employee"),
    # "Manager" is the same person as "Reports to", shaped as an employee
    # reference; a work row holds it once, as reportsTo.
    "work.manager": ("work", "reportsTo", "employee"),
    # HiBob tags a row "Other" when this is left out.
    "work.workChangeType": ("work", "workChangeType", "text"),
    "payroll.employment.contract": ("employment", "contract", "text"),
    "payroll.employment.type": ("employment", "type", "text"),
    "payroll.employment.salaryPayType": ("employment", "salaryPayType", "text"),
    "payroll.employment.flsaCode": ("employment", "flsaCode", "text"),
    "payroll.employment.calendarId": ("employment", "calendarId", "int"),
    "payroll.salary.payment": ("salary", "base", "amount"),
    "payroll.salary.payPeriod": ("salary", "payPeriod", "text"),
    "payroll.salary.payFrequency": ("salary", "payFrequency", "text"),
    "address.line1": ("address", "line1", "text"),
    "address.line2": ("address", "line2", "text"),
    "address.city": ("address", "city", "text"),
    "address.postCode": ("address", "postCode", "text"),
    "address.country": ("address", "country", "text"),
    "address.usaState": ("address", "usaState", "text"),
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
# Types the API cannot set, or this tool does not, and why.
NOT_WRITABLE_TYPES = {
    "document": "documents cannot be set through the API",
    "avatar": "avatars have their own upload endpoint",
    "period": "HiBob calculates it",
    "duration_with_unit": "HiBob calculates it",
    "working_pattern": "working patterns are not supported yet",
}
NOT_WRITABLE_PREFIXES = (
    ("peopleAnalytics.", "HiBob calculates it"),
    ("address.site", "it is the address of the employee's site, not their own"),
    # Answers 304 to a PUT, as a string or a number (checked live).
    ("employee.job", "it follows the employee's job profile"),
    (
        "internal.",
        "HiBob manages it; lifecycle changes have their own tools, such as "
        "hibob_terminate_employee",
    ),
)
# Fields kept as records rather than one dated row (phase 4 adds them).
RECORD_PREFIXES = (
    ("payroll.variable.", "variable pay"),
    ("payroll.entitlement.", "entitlement"),
    ("payroll.deduction.", "deduction"),
)
# Fields HiBob derives from others. Its metadata has no flag saying so
# (checked against a live tenant), so they are named here.
CALCULATED_FIELDS = frozenset(
    {
        "root.id",
        "root.fullName",
        "root.displayName",
        "root.creationDateTime",
        "work.activeEffectiveDate",
        "work.shortStartDate",
        "work.yearsOfService",
        "work.tenureYears",
        "work.tenureDurationYears",
        "work.isManager",
        "work.directReports",
        "work.indirectReports",
        "work.reportsTo.email",
        "work.reportsToIdInCompany",
        "work.secondLevelManager",
        "address.activeEffectiveDate",
        "address.fullAddress",
        "payroll.employment.activeEffectiveDate",
        "payroll.employment.fte",
        "payroll.employment.hoursInDayNotWorked",
        "payroll.employment.weeklyHours",
        "payroll.salary.activeEffectiveDate",
        "employee.jobProfileCode",
        "employee.jobProfileTitle",
        "employee.jobProfileDescription",
        "employee.hasPaidPayslip",
        "employee.ukTaxSettingsLockedByP6",
        "employee.isTaxYearReset",
        "employee.recentLeaveStartDate",
        "employee.recentLeaveEndDate",
        "employee.firstDayOfWork",
        "employee.lastDayOfWork",
    }
)
# Fields showing another's value under their own ID. work.site is the work
# row's site as plain text (a PUT to it answers 304); it is written as siteId.
FIELD_ALIASES = {"work.site": "work.siteId"}

RouteKind = Literal["field", "dated", "email", "start_date", "not_writable"]


@dataclass(frozen=True)
class PeopleField:
    id: str
    label: str
    category: str
    category_id: str
    type: str
    list_id: str | None
    json_path: str
    historical: bool
    calculated: bool

    @property
    def qualified_label(self) -> str:
        return f"{self.category} > {self.label}" if self.category else self.label


@dataclass(frozen=True)
class Route:
    kind: RouteKind
    table: str | None = None
    reason: str | None = None
    column: str | None = None
    wire: str | None = None


def canonical_field_id(text: Any) -> str:
    """A field ID in HiBob's dotted form, from any spelling a caller uses.

    "/work/title" is "work.title"; "firstName" and "/root/firstName" are
    "root.firstName".
    """
    raw = str(text or "").strip()
    if raw.startswith("/"):
        raw = raw[1:].replace("/", ".")
    if raw and "." not in raw:
        raw = ROOT_PREFIX + raw
    return raw


def normalize_people_fields(payload: Any) -> list[PeopleField]:
    """The fields in a metadata response: a list, or one under "fields"."""
    if isinstance(payload, dict):
        payload = payload.get("fields")
    items = payload if isinstance(payload, list) else []
    present = {
        canonical_field_id(item["id"])
        for item in items
        if isinstance(item, dict) and item.get("id")
    }
    fields: list[PeopleField] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        field_id = canonical_field_id(item["id"])
        if FIELD_ALIASES.get(field_id) in present:
            continue
        type_data = item.get("typeData")
        list_id = type_data.get("listId") if isinstance(type_data, dict) else None
        fields.append(
            PeopleField(
                id=field_id,
                label=str(item.get("name") or field_id).strip(),
                category=str(item.get("categoryDisplayName") or "").strip(),
                category_id=str(item.get("categoryId") or "").strip(),
                type=str(item.get("type") or "").strip(),
                list_id=list_id if isinstance(list_id, str) and list_id else None,
                json_path=str(item.get("jsonPath") or field_id).strip(),
                historical=item.get("historical") is True,
                calculated=item.get("calculated") is True,
            )
        )
    return fields


def find_fields(fields: list[PeopleField], text: Any) -> list[PeopleField]:
    """The fields ``text`` names exactly: by ID in any spelling, else by label.

    A label several categories share returns all of them; "Category > Label"
    picks one.
    """
    wanted = str(text or "").strip()
    if not wanted:
        raise ValueError("A field name cannot be empty.")
    field_id = canonical_field_id(wanted)
    field_id = FIELD_ALIASES.get(field_id, field_id)
    by_id = [field for field in fields if field.id == field_id]
    if by_id:
        return by_id
    lowered = " ".join(wanted.lower().split())
    return [
        field
        for field in fields
        if field.label.lower() == lowered or field.qualified_label.lower() == lowered
    ]


def nearest_fields(
    fields: list[PeopleField], text: Any, limit: int = MAX_CANDIDATES
) -> list[PeopleField]:
    """Fields to offer for a name that matched none."""
    leaves = [{"id": field.id, "name": field.qualified_label} for field in fields]
    by_id = {field.id: field for field in fields}
    return [by_id[leaf["id"]] for leaf in rank_matches(leaves, text)[:limit]]


def route_for(field: PeopleField) -> Route:
    """How HiBob takes a change to ``field``."""
    if field.id == EMAIL_FIELD:
        return Route("email")
    if field.id == START_DATE_FIELD:
        return Route("start_date")
    if field.calculated or field.id in CALCULATED_FIELDS:
        return Route("not_writable", reason="HiBob calculates it")
    if field.type in NOT_WRITABLE_TYPES:
        return Route("not_writable", reason=NOT_WRITABLE_TYPES[field.type])
    for prefix, reason in NOT_WRITABLE_PREFIXES:
        if field.id.startswith(prefix):
            return Route("not_writable", reason=reason)
    for prefix, record in RECORD_PREFIXES:
        if field.id.startswith(prefix):
            return Route(
                "not_writable",
                reason=f"it is kept as {record} records, and adding those is "
                "not supported yet",
            )
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
        return Route(
            "not_writable", reason="HiBob's API has no way to change this dated field"
        )
    return Route("field")


def describe_field(field: PeopleField) -> dict[str, Any]:
    """A field as hibob_list_employee_fields shows it."""
    route = route_for(field)
    entry: dict[str, Any] = {
        "id": field.id,
        "label": field.label,
        "category": field.category,
        "type": field.type,
        "write": route.kind,
    }
    if field.list_id:
        entry["list"] = field.list_id
    if route.table:
        entry["table"] = route.table
    if route.reason:
        entry["reason"] = route.reason
    return entry


def normalize_custom_tables(payload: Any) -> list[dict[str, Any]]:
    """Custom tables from GET /people/custom-tables/metadata, with columns."""
    tables = payload.get("tables") if isinstance(payload, dict) else payload
    out: list[dict[str, Any]] = []
    for table in tables if isinstance(tables, list) else []:
        if not isinstance(table, dict) or not table.get("id"):
            continue
        columns: list[dict[str, Any]] = []
        for column in table.get("columns") or []:
            if not isinstance(column, dict) or not column.get("id"):
                continue
            type_data = column.get("typeData")
            list_id = type_data.get("listId") if isinstance(type_data, dict) else None
            entry: dict[str, Any] = {
                "id": str(column["id"]),
                "label": str(column.get("name") or column["id"]),
                "type": column.get("type"),
                "required": column.get("mandatory") is True,
            }
            if list_id:
                entry["list"] = list_id
            columns.append(entry)
        out.append(
            {
                "id": str(table["id"]),
                "name": str(table.get("name") or table["id"]),
                "category": table.get("category"),
                "columns": columns,
            }
        )
    return out


def _slash(field_id: str) -> str:
    return "/" + field_id.replace(".", "/")


def _walk(node: Any, path: str) -> tuple[bool, Any]:
    if path.startswith(ROOT_PREFIX):
        path = path[len(ROOT_PREFIX) :]
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _cell(node: Any, field_id: str, json_path: str) -> tuple[bool, Any]:
    if isinstance(node, dict) and _slash(field_id) in node:
        cell = node[_slash(field_id)]
        if isinstance(cell, dict) and "value" in cell:
            return True, cell["value"]
        return True, cell
    return _walk(node, json_path)


def read_field(
    record: Any, field_id: str, json_path: str | None = None
) -> tuple[Any, Any]:
    """A field's value and display label from a people read, or (None, None).

    Values may be keyed by slash path ({"/work/title": {"value": ...}}) or
    nested by category ({"work": {"title": ...}}); display labels sit under
    "humanReadable" in the same shape, or beside a slash-keyed value.
    """
    if not isinstance(record, dict):
        return None, None
    path = json_path or field_id
    _, value = _cell(record, field_id, path)
    found, display = _cell(record.get("humanReadable"), field_id, path)
    if not found:
        cell = record.get(_slash(field_id))
        display = cell.get("humanReadable") if isinstance(cell, dict) else None
    return value, display
