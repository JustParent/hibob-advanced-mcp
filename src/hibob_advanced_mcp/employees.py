"""HiBob employee lifecycle tools: terminate an employee.

HiBob's API documents no way to undo a termination, so everything that can be
checked is checked before the one request is sent: the dates, the employee
(who must be found, and only once) and the reasons, which HiBob takes as list
item IDs and are resolved here from the names a user would give.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Annotated, Any, Literal
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .cache import NamedListCache
from .client import HiBobClient, get_client
from .employee_directory import (
    EMPLOYEE_REF_DESCRIPTION,
    EmployeeMatch,
    describe_candidates,
    find_employee,
)
from .envelopes import iso_date
from .errors import HiBobApiError, format_exception
from .list_values import named_list_items, resolve_list_values
from .people_api import (
    HISTORY_TABLES,
    custom_tables,
    people_fields,
    read_employee,
    read_table,
)
from .people_fields import (
    PeopleField,
    describe_field,
    find_fields,
    nearest_fields,
    read_field,
)
from .references import NOTHING_WRITTEN
from .tasks import find_employees

TERMINATE_PATH = "/employees/{employee_id}/terminate"
NAMED_LIST_PATH = "/company/named-lists/{name}"
# HiBob's list names are case-sensitive; this one is all lower case.
TERMINATION_REASON_LIST = "terminationreason"
REASON_TYPE_LIST = "lifecycleReasonType"


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


def _notice_period(length: int | None, unit: str | None) -> dict[str, Any] | None:
    """HiBob's notice period, given both its parts or neither."""
    if length is None and unit is None:
        return None
    if unit is None:
        raise ValueError("notice_period_length needs a notice_period_unit.")
    if length is None:
        raise ValueError("notice_period_unit needs a notice_period_length.")
    if length < 0:
        raise ValueError(f"notice_period_length must not be negative, not {length}.")
    return {"unit": unit, "length": length}


async def _employee(client: HiBobClient, employee: str) -> dict[str, Any]:
    """The one active employee with this ID or work email."""
    text = str(employee or "").strip()
    if not text:
        raise ValueError("employee must not be empty.")
    if "@" in text:
        text = text.lower()
        found = await find_employees(client, "root.email", text)
    else:
        found = await find_employees(client, "root.id", text)
    if not found:
        raise ValueError(
            f"No active employee has the ID or work email {text!r}. {NOTHING_WRITTEN}"
        )
    if len(found) > 1:
        names = ", ".join(f"{e['name']} ({e['id']})" for e in found)
        raise ValueError(
            f"{text!r} matches {len(found)} employees: {names}. Ask the user "
            f"which one, then pass their employee ID. {NOTHING_WRITTEN}"
        )
    return found[0]


async def _list_item_id(client: HiBobClient, list_name: str, value: Any) -> str:
    """The ID of the item of ``list_name`` named, or identified by, ``value``."""
    try:
        payload = await client.get(NAMED_LIST_PATH.format(name=list_name))
    except HiBobApiError as exc:
        if exc.status_code == 403:
            raise HiBobApiError(
                f"HiBob denied reading its {list_name} list (403), so the "
                "reason could not be checked. Check that the service user can "
                "view the Lifecycle category under People's data > People's "
                f"fields. {NOTHING_WRITTEN}",
                status_code=403,
                hibob_key=exc.hibob_key,
                hibob_error=exc.hibob_error,
            ) from exc
        raise
    items = named_list_items(payload)
    match = resolve_list_values(items, [value])
    if match["complete"]:
        return str(match["values"][0])
    problem = (match["ambiguous"] or match["unmatched"])[0]
    offered = (
        ", ".join(f"{c['name']!r} ({c['id']})" for c in problem["candidates"]) or "none"
    )
    how = "matches several" if match["ambiguous"] else "matches no"
    raise ValueError(
        f"{problem['name']!r} {how} items of HiBob's {list_name} list. "
        f"Closest: {offered}. {NOTHING_WRITTEN}"
    )


DEFAULT_EMPLOYEE_FIELDS = (
    "root.displayName",
    "root.email",
    "work.title",
    "work.department",
    "work.site",
    "work.reportsTo",
    "work.startDate",
    "internal.status",
)


def require_employee(match: EmployeeMatch, ref: Any) -> dict[str, Any]:
    """The matched employee, or a ValueError naming who it could be."""
    if match.employee:
        return match.employee
    if match.ambiguous:
        raise ValueError(
            f"{str(ref)!r} matches several employees: "
            f"{describe_candidates(match.candidates)}. Ask the user which one, "
            "then pass their ID or work email."
        )
    hint = (
        f" Closest: {describe_candidates(match.candidates)}."
        if match.candidates
        else ""
    )
    raise ValueError(f"No employee found for {str(ref)!r}.{hint}")


def _placeholder(field_id: str) -> PeopleField:
    """A default field the metadata did not describe, read by its ID."""
    return PeopleField(
        id=field_id,
        label=field_id,
        category="",
        category_id="",
        type="",
        list_id=None,
        json_path=field_id,
        historical=False,
        calculated=False,
    )


def _fields_to_read(
    known: list[PeopleField], wanted: list[str] | None
) -> list[PeopleField]:
    if not wanted:
        defaults = [
            find_fields(known, field_id) for field_id in DEFAULT_EMPLOYEE_FIELDS
        ]
        return [
            found[0] if len(found) == 1 else _placeholder(field_id)
            for field_id, found in zip(DEFAULT_EMPLOYEE_FIELDS, defaults, strict=True)
        ]
    chosen: list[PeopleField] = []
    problems: list[str] = []
    for text in wanted:
        matches = find_fields(known, text)
        if len(matches) == 1:
            chosen.append(matches[0])
            continue
        offered = matches or nearest_fields(known, text)
        names = ", ".join(f"{f.qualified_label} ({f.id})" for f in offered) or "none"
        what = "matches several fields" if matches else "matches no field"
        problems.append(f"{text!r} {what}; candidates: {names}")
    if problems:
        raise ValueError(
            "; ".join(problems) + ". hibob_list_employee_fields lists every field."
        )
    return chosen


async def _history(
    api: HiBobClient, cache: NamedListCache, employee_id: str, names: list[str]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in names:
        key = " ".join(str(name).lower().split())
        try:
            if key in HISTORY_TABLES:
                out[name] = await read_table(api, employee_id, HISTORY_TABLES[key])
                continue
            tables = await custom_tables(api, cache)
            table = next(
                (t for t in tables if key in (t["id"].lower(), t["name"].lower())), None
            )
            if table is None:
                known = sorted({*HISTORY_TABLES, *(t["name"] for t in tables)})
                out[name] = {
                    "error": f"No table called {name!r}. Tables: {', '.join(known)}."
                }
                continue
            out[name] = await read_table(api, employee_id, table["id"], custom=True)
        except Exception as exc:
            out[name] = {"error": format_exception(exc)}
    return out


def register_employee_tools(
    mcp: FastMCP,
    *,
    read_only: bool = False,
    client_factory: Callable[[], HiBobClient] = get_client,
    list_cache: NamedListCache | None = None,
) -> None:
    """Register the employee tools; the write tools are omitted when ``read_only``."""
    cache = list_cache if list_cache is not None else NamedListCache()
    read_annotations = dict(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )

    @mcp.tool(
        name="hibob_list_employee_fields",
        annotations=ToolAnnotations(
            title="List HiBob employee fields", **read_annotations
        ),
    )
    async def hibob_list_employee_fields(
        search: Annotated[
            str | None,
            Field(
                description=(
                    "Keep only fields whose label, ID or category contains this "
                    "text, and custom tables whose name or columns do."
                )
            ),
        ] = None,
    ) -> str:
        """List employee fields and custom tables, and how each is changed.

        Each field gives its id, label, category, type, list (the named list
        its values come from) and "write": "field" (changed directly),
        "dated" with "table" (work, employment or salary: changed from an
        effective date), "email" or "start_date" (their own endpoints), or
        "not_writable" with a "reason". Custom tables come with their columns
        and which are required.

        Returns:
            str: JSON {"count": N, "fields": [...], "custom_tables": [...]},
            with "custom_tables_error" if they could not be read, or an
            error beginning "Error:".
        """
        try:
            api = client_factory()
            fields = await people_fields(api, cache)
            result: dict[str, Any] = {}
            try:
                tables = await custom_tables(api, cache)
            except Exception as exc:
                tables = []
                result["custom_tables_error"] = format_exception(exc)
            text = (search or "").strip().lower()
            if text:
                fields = [
                    f
                    for f in fields
                    if text in f.label.lower()
                    or text in f.id.lower()
                    or text in f.category.lower()
                ]
                tables = [
                    t
                    for t in tables
                    if text in t["name"].lower()
                    or any(text in c["label"].lower() for c in t["columns"])
                ]
            result.update(
                {
                    "count": len(fields),
                    "fields": [describe_field(f) for f in fields],
                    "custom_tables": tables,
                }
            )
            return _dump(result)
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_get_employee",
        annotations=ToolAnnotations(
            title="Get a HiBob employee's data", **read_annotations
        ),
    )
    async def hibob_get_employee(
        employee: Annotated[
            str | int,
            Field(description=EMPLOYEE_REF_DESCRIPTION),
        ],
        fields: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Fields to read, by label ('Job title') or ID ('work.title'). "
                    "Omit for name, email, title, department, site, manager, "
                    "start date and status."
                )
            ),
        ] = None,
        history: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Tables whose rows to include: work, employment, salary, "
                    "lifecycle, variable pay, equity, training, bank accounts, "
                    "or a custom table's name or ID."
                )
            ),
        ] = None,
    ) -> str:
        """Read an employee's current values and, optionally, table history.

        Each field comes back as {"field", "id", "value", "display"}: "value"
        is what HiBob stores (list item IDs, employee IDs), "display" its
        label where HiBob gives one. Inactive employees are found by ID or
        email; names match active employees only. Each table in "history"
        gives its rows newest first and any "restricted_columns" the service
        user may not see, or an "error" for that table alone.

        Returns:
            str: JSON {"employee": {...}, "fields": [...], "history"?: {...}},
            or an error beginning "Error:".
        """
        try:
            api = client_factory()
            person = require_employee(
                await find_employee(api, cache, employee), employee
            )
            wanted = _fields_to_read(await people_fields(api, cache), fields)
            record = await read_employee(
                api, person["id"], [f.id for f in wanted], human_readable=True
            )
            values = []
            for f in wanted:
                value, display = read_field(record, f.id, f.json_path)
                values.append(
                    {
                        "field": f.qualified_label,
                        "id": f.id,
                        "value": value,
                        "display": display,
                    }
                )
            result: dict[str, Any] = {"employee": person, "fields": values}
            if history:
                result["history"] = await _history(api, cache, person["id"], history)
            return _dump(result)
        except Exception as exc:
            return format_exception(exc)

    if read_only:
        return

    @mcp.tool(
        name="hibob_terminate_employee",
        annotations=ToolAnnotations(
            title="Terminate a HiBob employee",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def hibob_terminate_employee(
        employee: Annotated[
            str,
            Field(
                description=(
                    "The employee to terminate, by HiBob employee ID or work "
                    "email (see hibob_find_employee)."
                )
            ),
        ],
        termination_date: Annotated[
            str, Field(description="The termination date, YYYY-MM-DD.")
        ],
        last_day_of_work: Annotated[
            str | None,
            Field(
                description=(
                    "The last day worked, YYYY-MM-DD, on or before the "
                    "termination date."
                )
            ),
        ] = None,
        termination_reason: Annotated[
            str | int | None,
            Field(
                description=(
                    "The reason, by name ('Resigned') or ID, from HiBob's "
                    "terminationreason list."
                )
            ),
        ] = None,
        reason_type: Annotated[
            str | int | None,
            Field(
                description=(
                    "The reason type, by name ('End of Contract') or ID, from "
                    "HiBob's lifecycleReasonType list."
                )
            ),
        ] = None,
        notice_period_length: Annotated[
            int | None,
            Field(description="The notice period's length, with its unit."),
        ] = None,
        notice_period_unit: Annotated[
            Literal["days", "weeks", "month", "years"] | None,
            Field(description="The notice period's unit, in HiBob's spelling."),
        ] = None,
    ) -> str:
        """Terminate an employee in HiBob. Sent once, never retried.

        HiBob adds a termination entry to the employee's lifecycle, and their
        status changes to Terminated on the termination date. HiBob's API
        documents no way to undo this, so confirm the person, the date and the
        reason with the user first. This does not revoke the employee's access
        to Bob.

        The employee may be given by ID or work email, and must be found
        among active employees exactly once. Reasons may be given by name or
        ID and are matched exactly, ignoring case, against HiBob's lists. An
        unknown employee, an unmatched reason or an invalid date is refused
        before anything is sent.

        Returns:
            str: JSON {"status": "termination_added", "employee": {"id",
            "name", "email"}, "termination": {...}}, where "termination" is
            exactly what HiBob was sent, or an error beginning "Error:".

        Examples:
            - "Jane's last day is 30 October, she resigned" ->
              employee='jane@example.com', termination_date='2026-10-30',
              termination_reason='Resigned', once the user confirms.

        Rate limit: 10 requests/minute.
        """
        try:
            day = iso_date("termination_date", termination_date)
            body: dict[str, Any] = {"terminationDate": day}
            if last_day_of_work is not None:
                last_day = iso_date("last_day_of_work", last_day_of_work)
                if date.fromisoformat(last_day) > date.fromisoformat(day):
                    raise ValueError(
                        f"last_day_of_work {last_day} is after the termination "
                        f"date {day}. {NOTHING_WRITTEN}"
                    )
                body["lastDayOfWork"] = last_day
            notice = _notice_period(notice_period_length, notice_period_unit)

            api = client_factory()
            person = await _employee(api, employee)
            if termination_reason is not None:
                body["terminationReason"] = await _list_item_id(
                    api, TERMINATION_REASON_LIST, termination_reason
                )
            if reason_type is not None:
                body["reasonType"] = await _list_item_id(
                    api, REASON_TYPE_LIST, reason_type
                )
            if notice is not None:
                body["noticePeriod"] = notice

            path = TERMINATE_PATH.format(employee_id=quote(person["id"], safe=""))
            await api.post(path, body)
            return _dump(
                {"status": "termination_added", "employee": person, "termination": body}
            )
        except Exception as exc:
            return format_exception(exc)
