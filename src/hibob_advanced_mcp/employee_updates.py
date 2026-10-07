"""hibob_update_employee: change an employee's data, field by field.

A user names fields as HiBob shows them and gives values by name; this module
finds each field, decides how HiBob takes a change to it, resolves each value
to what HiBob stores, and asks back for anything it cannot pin down rather
than guessing. Every check runs before the first write, and each write is
sent once. Plain fields go in one PUT /people/{id}; the work email and start
date have endpoints of their own. Columns of effective-dated tables (work,
employment, salary) are refused until adding dated rows is supported.
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
from .employee_rows import put_body
from .employee_values import EMPLOYEE_TYPES, LIST_TYPES, NeedsInput, coerce_value
from .envelopes import iso_date
from .errors import format_exception
from .list_values import resolve_list_values
from .people_api import named_list, people_fields
from .people_fields import PeopleField, Route, find_fields, nearest_fields, route_for
from .references import NOTHING_WRITTEN

PUT_PATH = "/people/{employee_id}"

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


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


async def _resolve_value(
    api: HiBobClient, cache: NamedListCache, target: PeopleField, given: Any
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
    return coerce_value(target, given)


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
        route = route_for(target)
        if route.kind == "not_writable":
            plan.problems.append(
                f"{target.qualified_label} cannot be changed: {route.reason}."
            )
            continue
        if route.kind == "dated":
            plan.problems.append(
                f"{target.qualified_label} is kept in HiBob's {route.table} history; "
                "adding rows to it is not supported yet."
            )
            continue
        try:
            value = await _resolve_value(api, cache, target, given)
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


def _applied(change: Change) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "field": change.field.qualified_label,
        "id": change.field.id,
        "to": change.given,
        "via": "field",
    }
    if change.value != change.given:
        entry["sent"] = change.value
    return entry


async def _apply(
    api: HiBobClient, plan: Plan, reason: str | None, sleep: SleepFn
) -> dict[str, Any]:
    assert plan.employee is not None
    employee_id = quote(plan.employee["id"], safe="")
    body = put_body({c.field.json_path: c.value for c in plan.changes})
    await api.request_response(
        "PUT", PUT_PATH.format(employee_id=employee_id), json=body
    )
    return {
        "status": "updated",
        "employee": plan.employee,
        "applied": [_applied(change) for change in plan.changes],
    }


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
                    "YYYY-MM-DD, for changes HiBob keeps a dated history of. "
                    "Plain fields change immediately and cannot be scheduled."
                )
            ),
        ] = None,
        reason: Annotated[
            str | None,
            Field(
                description="Why the change is made; recorded with a start-date change."
            ),
        ] = None,
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
            str: JSON {"status": "updated", "employee", "applied": [{"field",
            "id", "to", "sent"?, "via"}]}, or "needs_input" as above, or an
            error beginning "Error:" for anything a question cannot fix
            (nothing is written then either).

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
            if plan.questions or plan.employee is None:
                return _dump(
                    {
                        "status": "needs_input",
                        "employee": plan.employee,
                        "questions": plan.questions,
                    }
                )
            _check_schedule(day, plan.changes)
            return _dump(await _apply(api, plan, reason, sleep))
        except Exception as exc:
            return format_exception(exc)
