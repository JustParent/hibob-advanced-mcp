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
from .employee_rows import put_body, same_value
from .employee_tables import to_wire
from .employee_values import EMPLOYEE_TYPES, LIST_TYPES, NeedsInput, coerce_value
from .envelopes import iso_date
from .errors import HiBobApiError, format_exception
from .list_values import resolve_list_values
from .people_api import named_list, people_fields, read_employee
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


def _applied(change: Change, before: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "field": change.field.qualified_label,
        "id": change.field.id,
        "to": change.given,
        "via": VIA[change.route.kind],
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


async def _send(
    api: HiBobClient,
    employee_id: str,
    kind: str,
    group: list[Change],
    reason: str | None,
) -> bool:
    """Send one write; False when HiBob reports it changed nothing (304)."""
    path_id = quote(employee_id, safe="")
    if kind == "field":
        body = put_body({c.field.json_path: c.value for c in group})
        response = await api.request_response(
            "PUT", PUT_PATH.format(employee_id=path_id), json=body
        )
        return response.status_code != 304
    if kind == "start_date":
        start: dict[str, Any] = {"startDate": group[0].value}
        if reason:
            start["reason"] = reason
        await api.post(START_DATE_PATH.format(employee_id=path_id), start)
        return True
    response = await api.request_response(
        "PUT", EMAIL_PATH.format(employee_id=path_id), json={"email": group[0].value}
    )
    return response.status_code != 304


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
    result["unconfirmed"] = [
        {
            "field": c.field.qualified_label,
            "sent": c.value,
            "read": seen.get(c.field.id),
        }
        for c in pending
    ]
    result["unconfirmed_note"] = (
        "HiBob may still be applying these (its reads can lag writes by up to 20 "
        "seconds), or it ignored them because the service user cannot edit them"
        + (
            f" (People's data > People's fields: Edit on {', '.join(categories)})"
            if categories
            else ""
        )
        + ". Check again with hibob_get_employee."
    )


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
    api: HiBobClient, plan: Plan, reason: str | None, sleep: SleepFn
) -> dict[str, Any]:
    assert plan.employee is not None
    employee_id = plan.employee["id"]
    before = await _current(api, employee_id, plan.changes)
    result: dict[str, Any] = {
        "status": "updated",
        "employee": plan.employee,
        "applied": [],
        "warnings": [],
    }
    groups = [
        (kind, [c for c in plan.changes if c.route.kind == kind])
        for kind in WRITE_ORDER
    ]
    groups = [(kind, group) for kind, group in groups if group]
    written: list[Change] = []
    for index, (kind, group) in enumerate(groups):
        labels = ", ".join(c.field.qualified_label for c in group)
        try:
            changed = await _send(api, employee_id, kind, group, reason)
        except Exception as exc:
            explained = _explain(exc, group)
            if index == 0:
                raise explained from exc
            result["status"] = "partial"
            result["failed"] = {
                "write": WRITE_NAMES[kind],
                "fields": [c.field.qualified_label for c in group],
                "error": format_exception(explained),
            }
            result["not_sent"] = [
                c.field.qualified_label for _, rest in groups[index + 1 :] for c in rest
            ]
            break
        if not changed:
            result["warnings"].append(
                f"HiBob changed nothing for {labels}: they already had these values, "
                "or the service user cannot change them this way."
            )
            continue
        written.extend(group)
        result["applied"].extend(_applied(c, before) for c in group)
        if kind == "email":
            result["warnings"].append(
                "HiBob sends the employee a verification email at the new address."
            )
    if written:
        await _confirm(api, employee_id, written, result, sleep)
    elif result["status"] == "updated":
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
            str: JSON {"status": "updated" | "unchanged" | "partial",
            "employee", "applied": [{"field", "id", "from"?, "to", "sent"?,
            "via"}], "warnings"?, "unconfirmed"?: [{"field", "sent", "read"}],
            "unconfirmed_note"?, "failed"?, "not_sent"?}; or "needs_input" as
            above; or an error beginning "Error:" (nothing written).

        Writes go in this order, each sent once: plain fields (one PUT), start
        date, then work email (HiBob emails the employee to verify it). If
        one fails the rest are not sent ("partial"); nothing is rolled back.
        Each change is read back; HiBob silently skips fields the service
        user may not edit and its reads lag, so a change still not visible
        after about 10 seconds is listed under "unconfirmed".

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
            if dated:
                raise ValueError(
                    "Adding rows to HiBob's dated tables is not supported yet. "
                    f"{NOTHING_WRITTEN}"
                )
            return _dump(await _apply(api, plan, reason, sleep))
        except Exception as exc:
            return format_exception(exc)
