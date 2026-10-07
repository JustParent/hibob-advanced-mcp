"""hibob_add_employee_record: add one row to a table that holds several.

Variable pay, entitlements, deductions, equity, training, bank accounts,
dependents, right-to-work documents and custom tables. Unlike a change to
the work, employment or salary table, a new record copies nothing: it is
built from the columns the user gives, so what it needs is asked for rather
than guessed. Everything is checked and read before the one write, which is
sent once, then read back.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .cache import NamedListCache
from .client import HiBobClient
from .employee_directory import EMPLOYEE_REF_DESCRIPTION, find_employee
from .employee_records import (
    RECORD_TYPES,
    Column,
    RecordType,
    as_field,
    body_for,
    compare_record,
    custom_record_type,
    find_column,
    find_identical,
    find_new_row,
    find_record_types,
    mask_text,
    masked,
)
from .employee_tables import to_wire
from .employee_updates import READ_BACK_DELAYS, SleepFn, resolve_value
from .employee_values import NeedsInput
from .envelopes import iso_date
from .errors import HiBobApiError, format_exception
from .list_values import list_item_names
from .people_api import custom_tables, named_list, read_bulk_rows, read_table
from .references import NOTHING_WRITTEN

WRITE_PATH = "/people/{employee_id}/{path}"
CUSTOM_WRITE_PATH = "/people/custom-tables/{employee_id}/{table}"
MAX_OPTIONS = 10


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


async def _record_types(api: HiBobClient, cache: NamedListCache) -> list[RecordType]:
    try:
        tables = await custom_tables(api, cache)
    except Exception:
        tables = []
    return [*RECORD_TYPES, *(custom_record_type(t) for t in tables)]


async def read_records(
    api: HiBobClient, employee_id: str, rt: RecordType
) -> list[dict[str, Any]]:
    """An employee's rows of a record type, newest first."""
    if rt.read == "bulk":
        return await read_bulk_rows(api, employee_id, rt.path)
    data = await read_table(api, employee_id, rt.path, custom=rt.custom)
    return data["rows"]


async def _options(
    api: HiBobClient, cache: NamedListCache, column: Column
) -> list[str]:
    """The names a list-backed column accepts, for a question."""
    if column.options:
        return list(column.options)
    if not column.list:
        return []
    try:
        names = list(
            list_item_names(await named_list(api, cache, column.list)).values()
        )
    except Exception:
        return []
    return names[:MAX_OPTIONS]


async def _value(
    api: HiBobClient,
    cache: NamedListCache,
    rt: RecordType,
    column: Column,
    given: Any,
) -> Any:
    """``given`` as HiBob takes it for ``column``, or NeedsInput / ValueError."""
    field = as_field(rt, column)
    if column.options and column.kind == "text":
        wanted = " ".join(str(given).lower().split())
        for option in column.options:
            if " ".join(option.lower().split()) == wanted:
                return option
        raise ValueError(
            f"{field.qualified_label} must be one of {', '.join(column.options)}, "
            f"not {given!r}."
        )
    value = await resolve_value(api, cache, field, given)
    if column.kind in ("list", "multi-list") and column.list and column.send != "id":
        items = await named_list(api, cache, column.list)
        names = list_item_names(items)
        ids = value if isinstance(value, list) else [value]
        if column.send == "name":
            sent = [names.get(str(i), str(i)) for i in ids]
            return sent if isinstance(value, list) else sent[0]
        if column.send == "int":
            return to_wire("int", value, field.qualified_label)
    return value


def _display(rt: RecordType, column: str, value: Any) -> Any:
    hidden = {c.id for c in rt.columns if c.sensitive}
    return mask_text(value) if column in hidden and value not in (None, "") else value


def _explain(exc: Exception, rt: RecordType) -> Exception:
    """A permission refusal, naming the table to grant."""
    if not isinstance(exc, HiBobApiError) or exc.status_code != 403:
        return exc
    return HiBobApiError(
        f"{exc} Grant Edit on the {rt.label} table under People's data > People's "
        "fields.",
        status_code=exc.status_code,
        hibob_key=exc.hibob_key,
        hibob_error=exc.hibob_error,
    )


async def _confirm(
    api: HiBobClient,
    employee_id: str,
    rt: RecordType,
    before: list[dict[str, Any]],
    entry_id: Any,
    sent: dict[str, Any],
    result: dict[str, Any],
    sleep: SleepFn,
) -> None:
    """Read the new record back and compare every column sent."""
    problems: list[dict[str, Any]] = []
    try:
        for delay in READ_BACK_DELAYS:
            if delay:
                await sleep(delay)
            after = await read_records(api, employee_id, rt)
            found = find_new_row(before, after, entry_id, sent)
            if found is None:
                problems = [{"column": "(the new record)", "sent": None, "read": None}]
                continue
            result.setdefault("entry_id", found.get("id"))
            problems = compare_record(sent, found)
            if not problems:
                result["verified"] = True
                return
    except Exception as exc:
        result["verification_error"] = format_exception(exc)
        return
    result["verified"] = False
    result["unconfirmed"] = [
        {
            "field": f"{rt.label} > {p['column']}",
            "sent": _display(rt, p["column"], p["sent"]),
            "read": _display(rt, p["column"], p["read"]),
        }
        for p in problems
    ]
    result["unconfirmed_note"] = (
        "HiBob may still be applying this (its reads can lag writes by up to 20 "
        "seconds), or it dropped a column it would not store. Check again with "
        "hibob_get_employee."
    )


def register_record_tools(
    mcp: FastMCP,
    *,
    client_factory: Callable[[], HiBobClient],
    cache: NamedListCache,
    sleep: SleepFn,
) -> None:
    @mcp.tool(
        name="hibob_add_employee_record",
        annotations=ToolAnnotations(
            title="Add a record to a HiBob employee",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def hibob_add_employee_record(
        employee: Annotated[str | int, Field(description=EMPLOYEE_REF_DESCRIPTION)],
        record_type: Annotated[
            str,
            Field(
                description=(
                    "The kind of record: Variable pay, Entitlement, Deduction, "
                    "Equity grant, Training, Bank account, Dependent, Right to "
                    "work, or the name of a custom table "
                    "(hibob_list_employee_fields lists them with their columns)."
                )
            ),
        ],
        values: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Column label or ID to its value, e.g. "
                    '{"Variable type": "Bonus", "Amount": {"value": 5000, '
                    '"currency": "GBP"}, "Payment period": "Annual"}. List '
                    "values by name, dates YYYY-MM-DD."
                )
            ),
        ],
        effective_date: Annotated[
            str | None,
            Field(
                description=(
                    "YYYY-MM-DD. Needed for variable pay, entitlements and "
                    "deductions, which HiBob dates; not allowed for the rest."
                )
            ),
        ] = None,
    ) -> str:
        """Add one record to an employee. Sent once, never retried.

        Records are rows HiBob keeps several of: variable pay, entitlements,
        deductions, equity grants, training, bank accounts, dependents,
        right-to-work documents and custom tables. A new record copies
        nothing, so the columns it needs are asked for (with the options a
        list offers) rather than guessed, as is a missing effective date. A
        value that matches nothing or several things comes back as a question
        too, and nothing is written. Confirm the details with the user
        first. Bank and document numbers are sent to HiBob in full and shown
        back masked.

        Calling again with the same values adds nothing: an identical record
        already there is reported instead, so a retry after a lost response
        is safe. The record is read back afterwards; a column HiBob dropped
        is listed under "unconfirmed".

        Returns:
            str: JSON {"status": "added" | "unchanged", "employee",
            "record_type", "entry_id"?, "row", "verified"?, "warnings"?,
            "unconfirmed"?, "unconfirmed_note"?}; or {"status":
            "needs_input", "questions": [...]}; or an error beginning
            "Error:" (nothing written).

        Rate limit: 10 writes/minute.
        """
        try:
            if not values:
                raise ValueError("values must name at least one column.")
            day = iso_date("effective_date", effective_date) if effective_date else None
            api = client_factory()
            types = await _record_types(api, cache)
            found = find_record_types(types, record_type)
            if len(found) != 1:
                known = ", ".join(t.label for t in types)
                what = (
                    "matches several record types" if found else "is not a record type"
                )
                raise ValueError(
                    f"{record_type!r} {what}. The record types are: {known}. "
                    f"{NOTHING_WRITTEN}"
                )
            rt = found[0]
            questions: list[dict[str, Any]] = []
            problems: list[str] = []
            match = await find_employee(api, cache, employee)
            person = match.employee
            if person is None:
                if match.candidates:
                    questions.append(
                        {
                            "argument": "employee",
                            "question": (
                                f"Which employee did you mean by {str(employee)!r}?"
                            ),
                            "candidates": match.candidates,
                        }
                    )
                else:
                    problems.append(f"No employee found for {str(employee)!r}.")
            who = (person or {}).get("name") or "the employee"
            sent: dict[str, Any] = {}
            seen: dict[str, str] = {}
            for key, given in values.items():
                column = find_column(rt, key)
                if column is None:
                    questions.append(
                        {
                            "argument": "values",
                            "key": key,
                            "question": (
                                f"Which {rt.label} column did you mean by {key!r}?"
                            ),
                            "candidates": [
                                {"id": c.id, "label": c.label} for c in rt.columns
                            ],
                        }
                    )
                    continue
                if column.id in seen:
                    problems.append(
                        f"{rt.label} > {column.label} is given twice, as "
                        f"{seen[column.id]!r} and {key!r}."
                    )
                    continue
                seen[column.id] = key
                try:
                    sent[column.id] = await _value(api, cache, rt, column, given)
                except NeedsInput as need:
                    questions.append({"key": key, **need.question})
                except ValueError as exc:
                    problems.append(str(exc))
            if problems:
                raise ValueError(" ".join(problems) + f" {NOTHING_WRITTEN}")
            if rt.dated and day is None:
                questions.append(
                    {
                        "argument": "effective_date",
                        "question": (
                            f"From what date should the {rt.label} record for "
                            f"{who} apply?"
                        ),
                    }
                )
            if not rt.dated and day is not None:
                raise ValueError(
                    f"{rt.label} records have no effective date, so effective_date "
                    f"does not apply. {NOTHING_WRITTEN}"
                )
            missing = [c for c in rt.columns if c.required and c.id not in sent]
            if missing and not any(
                q.get("key") for q in questions if q.get("argument") == "values"
            ):
                described = []
                for column in missing:
                    entry: dict[str, Any] = {
                        "column": column.id,
                        "label": column.label,
                        "type": column.kind,
                    }
                    options = await _options(api, cache, column)
                    if options:
                        entry["options"] = options
                    described.append(entry)
                questions.append(
                    {
                        "argument": "values",
                        "question": (
                            f"To add a {rt.label} record for {who} I also need: "
                            f"{', '.join(c.label for c in missing)}."
                        ),
                        "missing": described,
                    }
                )
            if questions or person is None:
                return _dump(
                    {
                        "status": "needs_input",
                        "employee": person,
                        "questions": questions,
                    }
                )
            employee_id = person["id"]
            row = dict(sent)
            if rt.dated and day:
                row["effectiveDate"] = day
            before = await read_records(api, employee_id, rt)
            result: dict[str, Any] = {
                "employee": person,
                "record_type": rt.label,
                "row": masked(rt, row),
            }
            same = find_identical(before, row)
            if same is not None:
                result["status"] = "unchanged"
                result["entry_id"] = same.get("id")
                result["warnings"] = [
                    f"{who} already has this {rt.label} record (entry "
                    f"{same.get('id')}), so nothing was added."
                ]
                return _dump(result)
            quoted = quote(employee_id, safe="")
            path = (
                CUSTOM_WRITE_PATH.format(
                    employee_id=quoted, table=quote(rt.path, safe="")
                )
                if rt.custom
                else WRITE_PATH.format(employee_id=quoted, path=rt.path)
            )
            try:
                response = await api.post(path, body_for(rt, sent, day))
            except Exception as exc:
                raise _explain(exc, rt) from exc
            entry_id = response.get("entryId") if isinstance(response, dict) else None
            result["status"] = "added"
            if entry_id is not None:
                result["entry_id"] = entry_id
            await _confirm(api, employee_id, rt, before, entry_id, row, result, sleep)
            return _dump(result)
        except Exception as exc:
            return format_exception(exc)
