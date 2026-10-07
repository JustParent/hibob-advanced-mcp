"""hibob_update_employee: change an employee's data, field by field.

A user names fields as HiBob shows them and gives values by name; this module
finds each field, decides how HiBob takes a change to it, resolves each value
to what HiBob stores, and asks back for anything it cannot pin down rather
than guessing. Every check runs before the first write, and each write is
sent once. Plain fields go in one PUT /people/{id}; the work email and start
date have endpoints of their own. Columns of effective-dated tables (work,
employment, salary) are written as new rows that copy the row before their date.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Any
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .cache import NamedListCache
from .client import HiBobClient
from .employee_directory import EMPLOYEE_REF_DESCRIPTION, find_employee
from .employee_rows import put_body, same_value
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
from .employee_values import EMPLOYEE_TYPES, LIST_TYPES, NeedsInput, coerce_value
from .envelopes import iso_date
from .errors import HiBobApiError, format_exception
from .list_values import resolve_list_values
from .people_api import named_list, people_fields, read_employee, read_table
from .people_fields import (
    PeopleField,
    Route,
    find_fields,
    nearest_fields,
    read_field,
    route_for,
)
from .references import NOTHING_WRITTEN

PUT_PATH = "/people/{employee_id}"
EMAIL_PATH = "/people/{employee_id}/email"
START_DATE_PATH = "/employees/{employee_id}/start-date"
ROW_PATH = "/people/{employee_id}/{table}"
# A table with no earlier salary row needs these to start one. HiBob's
# reference lists the first two as required; it also refuses a row without a
# pay frequency ("Missing pay frequency", seen live).
FIRST_SALARY_COLUMNS = (
    ("base", "the amount with its currency"),
    ("payPeriod", "the pay period, for example Annual"),
    ("payFrequency", "the pay frequency, for example Monthly"),
)
# Email goes last: HiBob sends the employee a verification email.
WRITE_ORDER = ("field", "start_date", "email")
WRITE_NAMES = {"field": "fields", "start_date": "start date", "email": "work email"}
VIA = {"field": "field", "start_date": "start date endpoint", "email": "email endpoint"}
# HiBob's reads showed writes within 0.7 s on a live tenant (its docs allow up
# to 20 s); a change still unseen after these waits is reported unconfirmed.
READ_BACK_DELAYS = (0.0, 1.0, 3.0, 6.0)

SleepFn = Callable[[float], Awaitable[None]]


@dataclass
class Change:
    key: str
    field: PeopleField
    route: Route
    given: Any
    value: Any


@dataclass
class Plan:
    employee: dict[str, Any] | None = None
    changes: list[Change] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


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


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


async def _resolve_value(
    api: HiBobClient,
    cache: NamedListCache,
    target: PeopleField,
    given: Any,
    *,
    bare_amount_ok: bool = False,
) -> Any:
    if given is None:
        return coerce_value(target, given)
    if target.type in LIST_TYPES:
        if not target.list_id:
            raise ValueError(
                f"{target.qualified_label} names no list, so its value cannot be "
                "checked."
            )
        wanted = given if isinstance(given, list) else [given]
        if target.type != "multi-list" and len(wanted) != 1:
            raise ValueError(
                f"{target.qualified_label} takes one value, not {given!r}."
            )
        match = resolve_list_values(
            await named_list(api, cache, target.list_id), wanted
        )
        if not match["complete"]:
            problem = (match["ambiguous"] or match["unmatched"])[0]
            raise NeedsInput(
                {
                    "argument": "changes",
                    "question": (
                        f"Which {target.label} did you mean by {problem['name']!r}?"
                    ),
                    "candidates": problem["candidates"],
                }
            )
        ids = match["values"]
        return ids if target.type == "multi-list" else ids[0]
    if target.type in EMPLOYEE_TYPES:
        found = await find_employee(api, cache, given)
        if found.employee:
            return found.employee["id"]
        if found.candidates:
            raise NeedsInput(
                {
                    "argument": "changes",
                    "question": f"Who did you mean by {given!r} for {target.label}?",
                    "candidates": found.candidates,
                }
            )
        raise ValueError(f"No employee found for {given!r} ({target.qualified_label}).")
    return coerce_value(target, given, bare_amount_ok=bare_amount_ok)


async def _plan(
    api: HiBobClient, cache: NamedListCache, employee: Any, changes: dict[str, Any]
) -> Plan:
    plan = Plan()
    match = await find_employee(api, cache, employee)
    if match.employee:
        plan.employee = match.employee
    elif match.candidates:
        plan.questions.append(
            {
                "argument": "employee",
                "question": f"Which employee did you mean by {str(employee)!r}?",
                "candidates": match.candidates,
            }
        )
    else:
        plan.problems.append(f"No employee found for {str(employee)!r}.")
    fields = await people_fields(api, cache)
    seen: dict[str, str] = {}
    for key, given in changes.items():
        matches = find_fields(fields, key)
        if len(matches) != 1:
            offered = matches or nearest_fields(fields, key)
            plan.questions.append(
                {
                    "argument": "changes",
                    "key": key,
                    "question": (
                        f"{key!r} could be several fields. Which one?"
                        if matches
                        else f"Which field did you mean by {key!r}? "
                        "hibob_list_employee_fields lists them all."
                    ),
                    "candidates": [
                        {"id": f.id, "label": f.qualified_label} for f in offered
                    ],
                }
            )
            continue
        target = matches[0]
        if target.id in seen:
            plan.problems.append(
                f"{target.qualified_label} is given twice, as {seen[target.id]!r} "
                f"and {key!r}."
            )
            continue
        seen[target.id] = key
        route = route_for(target)
        if route.kind == "not_writable":
            plan.problems.append(
                f"{target.qualified_label} cannot be changed: {route.reason}."
            )
            continue
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
        if route.kind == "email":
            value = str(value).lower()
            if "@" not in value:
                plan.problems.append(f"{given!r} is not an email address.")
                continue
        plan.changes.append(Change(key, target, route, given, value))
    return plan


def _date_question(plan: Plan, dated: list[Change]) -> dict[str, Any]:
    """The one question that covers every change HiBob keeps dated rows for."""
    who = (plan.employee or {}).get("name") or "the employee"
    names = ", ".join(change.field.label for change in dated)
    return {
        "argument": "effective_date",
        "question": f"From what date should {names} change for {who}?",
        "applies_to": [change.field.qualified_label for change in dated],
    }


def _check_schedule(day: str | None, changes: list[Change]) -> None:
    """Refuse to pretend an immediate change can wait for a later day."""
    if day is None or date.fromisoformat(day) == date.today():
        return
    names = [change.field.qualified_label for change in changes]
    if names:
        raise ValueError(
            f"HiBob changes {', '.join(names)} immediately and cannot schedule "
            f"them for {day}. Leave out effective_date to change them now, or ask "
            f"the user when to make the change. {NOTHING_WRITTEN}"
        )


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
    needs = (
        f"{', '.join(missing[:-1])} and {missing[-1]}"
        if len(missing) > 1
        else missing[0]
    )
    return [
        {
            "argument": "changes",
            "question": (
                f"{who} has no salary row before {day}, so a new one needs {needs}. "
                "Add them to changes."
            ),
            "applies_to": labels,
        }
    ]


def _later_question(
    who: str, table: TableSpec, conflicts: list[dict[str, Any]], labels: list[str]
) -> dict[str, Any]:
    first = conflicts[0]
    held = ", ".join(
        f"{column} {value!r}" for column, value in first["columns"].items()
    )
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
        if base is None and day > date.today().isoformat():
            warnings.append(
                f"HiBob counts a first {table.label} row as current whatever its "
                f"date, so {who}'s {table.label} already shows these values "
                f"before {day}."
            )
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
    writes = [Write(f"{row.table.label} row", "row", row.changes, row) for row in rows]
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


def register_update_tools(
    mcp: FastMCP,
    *,
    client_factory: Callable[[], HiBobClient],
    cache: NamedListCache,
    sleep: SleepFn,
) -> None:
    @mcp.tool(
        name="hibob_update_employee",
        annotations=ToolAnnotations(
            title="Update a HiBob employee's data",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def hibob_update_employee(
        employee: Annotated[
            str | int,
            Field(description=EMPLOYEE_REF_DESCRIPTION),
        ],
        changes: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Field label or ID to its new value, e.g. "
                    '{"Mobile phone": "07700 900123", "Shirt size": "Medium"}. '
                    "List values by name, people by name, email or ID, dates "
                    'YYYY-MM-DD, amounts as {"value": 5000, "currency": "GBP"}.'
                )
            ),
        ],
        effective_date: Annotated[
            str | None,
            Field(
                description=(
                    "YYYY-MM-DD. Needed for job title, department, site, manager, "
                    "employment and salary changes, which HiBob keeps as dated "
                    "rows; the tool asks for it if missing. Plain fields change "
                    "immediately and cannot be scheduled."
                )
            ),
        ] = None,
        reason: Annotated[
            str | None,
            Field(
                description=(
                    "Why the change is made; recorded on new work and employment "
                    "rows and with a start-date change (HiBob's salary table has "
                    "no reason column)."
                )
            ),
        ] = None,
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
    ) -> str:
        """Change an employee's data. Each write is sent once, never retried.

        Fields are named as HiBob shows them (hibob_list_employee_fields
        lists them) and values given by name: the tool finds each field,
        resolves list values and people to their IDs and sends each change
        to the right place. Anything it cannot pin down comes back as
        {"status": "needs_input", "questions": [...]} with candidates, and
        nothing is written; put the questions to the user and call again.
        Confirm the changes with the user before calling.

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

        Rate limit: 10 writes/minute.
        """
        try:
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
        except Exception as exc:
            return format_exception(exc)
