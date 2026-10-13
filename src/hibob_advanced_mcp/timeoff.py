"""HiBob time off tools: policy types, requests, balances, changes, booking and
cancelling.

What HiBob does, found live on 2026-10-13 (tests/fixtures/timeoff/observations.md):

- DELETE /timeoff/employees/{id}/requests/{requestId} cancels by request ID
  alone. The employee ID in the path is not checked, so another employee's
  request is cancelled just the same. A GET of that path answers 404 for
  another employee's request, so the cancel tool reads the request under the
  employee first and sends nothing on a 404.
- Pending and approved requests cancel alike, whether past, current or
  future, and the cancellation is immediate: the request reads "canceled" at
  once. A second DELETE of a cancelled request answers 200 and changes nothing.
- Who's out lists approved and pending requests only, as "approved" and
  "pending"; a cancelled request drops out of it. The request read calls the
  pending state "pending_approval".
- HiBob's API cannot approve or decline a request.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlencode

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .cache import NamedListCache
from .client import HiBobClient, get_client
from .employee_directory import EMPLOYEE_REF_DESCRIPTION, find_employee
from .employees import require_employee
from .envelopes import iso_date, normalize_id
from .errors import HiBobApiError, format_exception
from .references import NOTHING_WRITTEN

POLICY_TYPES_PATH = "/timeoff/policy-types"
POLICY_TYPE_PATH = "/timeoff/policy-types/{policy_type}"
REASON_CODES_PATH = "/timeoff/policy-types/{policy_type}/reason-codes"
POLICY_NAMES_PATH = "/timeoff/policies/names"
POLICY_PATH = "/timeoff/policies"
WHOS_OUT_PATH = "/timeoff/whosout"
CHANGES_PATH = "/timeoff/requests/changes"
BALANCE_PATH = "/timeoff/employees/{employee_id}/balance"
REQUESTS_PATH = "/timeoff/employees/{employee_id}/requests"
REQUEST_PATH = "/timeoff/employees/{employee_id}/requests/{request_id}"

CANCELLED = "canceled"
# The request read's states that a cancellation applies to.
CANCELLABLE = frozenset({"pending_approval", "approved"})
NOT_LISTED = (
    "Only approved and pending requests are listed: cancelled and declined ones "
    "are not. hibob_get_timeoff_request reads any request by ID, and "
    "hibob_get_timeoff_changes lists cancellations."
)

Portion = Literal["all_day", "morning", "afternoon"]


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


def _segment(value: object, what: str) -> str:
    """A non-empty value, URL-quoted for use as a path segment."""
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ValueError(f"{what} must not be empty.")
    return quote(text, safe="")


def _request_id(value: object) -> str:
    text = normalize_id(value) if value is not None else ""
    if not text.isdigit():
        raise ValueError(
            f"request_id must be HiBob's numeric request ID, not {value!r}. "
            "hibob_list_timeoff_requests lists them."
        )
    return text


def _with_query(path: str, params: dict[str, Any]) -> str:
    return f"{path}?{urlencode(params)}"


def _flag(value: bool) -> str:
    return "true" if value else "false"


def _since(value: str) -> str:
    """HiBob's ``since``: a date becomes midnight UTC, a timestamp passes through."""
    text = str(value or "").strip()
    if "T" in text:
        return text
    return f"{iso_date('since', text)}T00:00:00.000Z"


def _request_summary(request: dict[str, Any]) -> dict[str, Any]:
    """What a person needs to recognise a request."""
    keys = (
        "policyTypeDisplayName",
        "status",
        "startDate",
        "startPortion",
        "endDate",
        "endPortion",
        "totalDuration",
        "durationUnit",
        "description",
    )
    return {key: request[key] for key in keys if request.get(key) is not None}


def _start(request: dict[str, Any]) -> date | None:
    """The day a request starts. Hourly requests carry ``date`` instead."""
    raw = request.get("startDate") or request.get("date")
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


def register_timeoff_tools(
    mcp: FastMCP,
    *,
    read_only: bool = False,
    hide_people_data: bool = False,
    client_factory: Callable[[], HiBobClient] = get_client,
    list_cache: NamedListCache | None = None,
    today: Callable[[], date] = date.today,
) -> None:
    """Register the time off tools.

    The write tools are omitted when ``read_only``. With ``hide_people_data``
    only the policy type tool is registered: every other tool shows people's
    absences, sickness included.
    """
    cache = list_cache if list_cache is not None else NamedListCache()
    read_annotations = dict(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )

    async def _person(api: HiBobClient, employee: str) -> dict[str, Any]:
        person = require_employee(await find_employee(api, cache, employee), employee)
        return {key: person.get(key) for key in ("id", "name", "email")}

    async def _read_request(
        api: HiBobClient, person: dict[str, Any], request_id: str
    ) -> dict[str, Any]:
        path = REQUEST_PATH.format(
            employee_id=_segment(person["id"], "employee"), request_id=request_id
        )
        try:
            found = await api.get(path)
        except HiBobApiError as exc:
            if exc.status_code == 404:
                raise ValueError(
                    f"{person['name'] or person['id']} has no time off request "
                    f"{request_id}. HiBob answers 404 for a wrong ID and for "
                    "another employee's request alike; "
                    "hibob_list_timeoff_requests lists this employee's requests."
                ) from exc
            raise
        if not isinstance(found, dict):
            raise ValueError(f"HiBob returned no details for request {request_id}.")
        return found

    @mcp.tool(
        name="hibob_list_timeoff_policy_types",
        annotations=ToolAnnotations(
            title="List HiBob time off policy types", **read_annotations
        ),
    )
    async def hibob_list_timeoff_policy_types(
        policy_type: Annotated[
            str | None,
            Field(
                description=(
                    "A policy type's name, such as 'Holiday', to describe it. "
                    "Omit to list every type's name."
                )
            ),
        ] = None,
    ) -> str:
        """List time off policy types, or describe one.

        Without policy_type, returns {"policy_types": [names]}. With one,
        returns its unit (days or hours), the policies of that type (each with
        approvalRequired, allowance and minimum request) and its reason codes,
        which some types require when booking.

        Returns:
            str: JSON, or an error beginning "Error:".
        """
        try:
            api = client_factory()
            if not (policy_type and policy_type.strip()):
                payload = await api.get(POLICY_TYPES_PATH)
                types = payload.get("policyTypes") if isinstance(payload, dict) else []
                return _dump({"policy_types": types or []})
            name = policy_type.strip()
            segment = _segment(name, "policy_type")
            details = await api.get(POLICY_TYPE_PATH.format(policy_type=segment))
            names = await api.get(
                _with_query(POLICY_NAMES_PATH, {"policyTypeName": name})
            )
            policies = [
                await api.get(_with_query(POLICY_PATH, {"policyName": policy}))
                for policy in (names or {}).get("policies") or []
            ]
            codes = await api.get(REASON_CODES_PATH.format(policy_type=segment))
            return _dump(
                {
                    "policy_type": details,
                    "policies": policies,
                    "reason_codes": (codes or {}).get("reasonCodes") or [],
                }
            )
        except Exception as exc:
            return format_exception(exc)

    if hide_people_data:
        return

    @mcp.tool(
        name="hibob_list_timeoff_requests",
        annotations=ToolAnnotations(
            title="List HiBob time off requests (who's out)", **read_annotations
        ),
    )
    async def hibob_list_timeoff_requests(
        from_date: Annotated[str, Field(description="First day, YYYY-MM-DD.")],
        to_date: Annotated[str, Field(description="Last day, YYYY-MM-DD.")],
        employee: Annotated[
            str | None,
            Field(description=EMPLOYEE_REF_DESCRIPTION + " Omit for everyone."),
        ] = None,
        include_pending: Annotated[
            bool, Field(description="Include requests awaiting approval.")
        ] = True,
        include_private: Annotated[
            bool,
            Field(
                description=(
                    "Include private policies and those with a custom name, which "
                    "needs its own HiBob permission."
                )
            ),
        ] = True,
    ) -> str:
        """List time off overlapping a date range, with each request's ID and status.

        Each request gives requestId (what the other time off tools take),
        employeeId, employeeDisplayName, policyType, status ("approved" or
        "pending"), startDate, startPortion, endDate, endPortion and type.
        Only approved and pending requests are listed: cancelled and declined
        ones are not.

        Returns:
            str: JSON {"from", "to", "count", "requests": [...], "note"}, with
            "employee" when one was named, or an error beginning "Error:".
        """
        try:
            start = iso_date("from_date", from_date)
            end = iso_date("to_date", to_date)
            if end < start:
                raise ValueError(f"to_date {end} is before from_date {start}.")
            api = client_factory()
            person = await _person(api, employee) if employee else None
            payload = await api.get(
                _with_query(
                    WHOS_OUT_PATH,
                    {
                        "from": start,
                        "to": end,
                        "includeHourly": "true",
                        "includePending": _flag(include_pending),
                        "includePrivate": _flag(include_private),
                    },
                )
            )
            outs = payload.get("outs") if isinstance(payload, dict) else None
            requests = [out for out in outs or [] if isinstance(out, dict)]
            if person:
                requests = [
                    out
                    for out in requests
                    if normalize_id(out.get("employeeId")) == person["id"]
                ]
            result: dict[str, Any] = {"from": start, "to": end}
            if person:
                result["employee"] = person
            result.update(count=len(requests), requests=requests, note=NOT_LISTED)
            return _dump(result)
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_get_timeoff_request",
        annotations=ToolAnnotations(
            title="Get a HiBob time off request", **read_annotations
        ),
    )
    async def hibob_get_timeoff_request(
        employee: Annotated[str, Field(description=EMPLOYEE_REF_DESCRIPTION)],
        request_id: Annotated[
            str, Field(description="The numeric time off request ID.")
        ],
    ) -> str:
        """Read one time off request, whatever its status.

        status is "pending_approval", "approved" or "canceled" (HiBob's
        spelling), with dates, portions, duration, description, approval and
        decline details and per-day durations. A request belonging to someone
        else is reported as not found, as HiBob does.

        Returns:
            str: JSON {"employee": {...}, "request": {...}}, or an error
            beginning "Error:".
        """
        try:
            rid = _request_id(request_id)
            api = client_factory()
            person = await _person(api, employee)
            request = await _read_request(api, person, rid)
            return _dump({"employee": person, "request": request})
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_get_timeoff_balance",
        annotations=ToolAnnotations(
            title="Get a HiBob time off balance", **read_annotations
        ),
    )
    async def hibob_get_timeoff_balance(
        employee: Annotated[str, Field(description=EMPLOYEE_REF_DESCRIPTION)],
        policy_type: Annotated[
            str,
            Field(description="The policy type, such as 'Holiday'."),
        ],
        as_of: Annotated[
            str | None,
            Field(description="The day to work it out on, YYYY-MM-DD. Default today."),
        ] = None,
    ) -> str:
        """An employee's balance for one policy type on a given day.

        Gives the balance on that day, the annual allowance, the starting
        balance, days taken, adjustments and the policy that applies.

        Returns:
            str: JSON {"employee": {...}, "balance": {...}}, or an error
            beginning "Error:".
        """
        try:
            on = iso_date("as_of", as_of) if as_of else today().isoformat()
            kind = str(policy_type or "").strip()
            if not kind:
                raise ValueError("policy_type must not be empty.")
            api = client_factory()
            person = await _person(api, employee)
            path = BALANCE_PATH.format(employee_id=_segment(person["id"], "employee"))
            # HiBob answers a balance read without a date with its login page.
            balance = await api.get(_with_query(path, {"policyType": kind, "date": on}))
            return _dump({"employee": person, "balance": balance})
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_get_timeoff_changes",
        annotations=ToolAnnotations(
            title="List HiBob time off request changes", **read_annotations
        ),
    )
    async def hibob_get_timeoff_changes(
        since: Annotated[
            str,
            Field(
                description=(
                    "YYYY-MM-DD (midnight UTC) or an ISO timestamp. HiBob looks "
                    "back at most six months."
                )
            ),
        ],
        employee: Annotated[
            str | None,
            Field(description=EMPLOYEE_REF_DESCRIPTION + " Omit for everyone."),
        ] = None,
    ) -> str:
        """Requests created or cancelled since a moment, newest state per request.

        Each change gives changeType ("Created" or "Canceled"), requestId,
        the employee, policy type, dates and createdOn. This is how a
        cancelled request can be found, since who's out drops them.

        Returns:
            str: JSON {"since", "count", "changes": [...]}, with "employee"
            when one was named, or an error beginning "Error:".
        """
        try:
            moment = _since(since)
            api = client_factory()
            person = await _person(api, employee) if employee else None
            payload = await api.get(_with_query(CHANGES_PATH, {"since": moment}))
            raw = payload.get("changes") if isinstance(payload, dict) else payload
            changes = [c for c in raw or [] if isinstance(c, dict)]
            if person:
                changes = [
                    c
                    for c in changes
                    if normalize_id(c.get("employeeId")) == person["id"]
                ]
            result: dict[str, Any] = {"since": moment}
            if person:
                result["employee"] = person
            result.update(count=len(changes), changes=changes)
            return _dump(result)
        except Exception as exc:
            return format_exception(exc)

    if read_only:
        return

    @mcp.tool(
        name="hibob_submit_timeoff_request",
        annotations=ToolAnnotations(
            title="Book time off in HiBob",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def hibob_submit_timeoff_request(
        employee: Annotated[str, Field(description=EMPLOYEE_REF_DESCRIPTION)],
        policy_type: Annotated[
            str,
            Field(
                description=(
                    "The policy type, such as 'Holiday' (see "
                    "hibob_list_timeoff_policy_types)."
                )
            ),
        ],
        start_date: Annotated[str, Field(description="First day, YYYY-MM-DD.")],
        end_date: Annotated[
            str | None,
            Field(description="Last day, YYYY-MM-DD. Omit for a single day."),
        ] = None,
        start_portion: Annotated[
            Portion,
            Field(
                description=(
                    "all_day, or a half day: 'afternoon' to start at midday. For "
                    "a single half day give the same portion for start and end."
                )
            ),
        ] = "all_day",
        end_portion: Annotated[
            Portion,
            Field(description="all_day, or 'morning' to finish at midday."),
        ] = "all_day",
        description: Annotated[
            str | None, Field(description="A note shown on the request.")
        ] = None,
        reason_code: Annotated[
            int | None,
            Field(
                description=(
                    "The reason code ID, for policy types that require one (see "
                    "hibob_list_timeoff_policy_types)."
                )
            ),
        ] = None,
        skip_manager_approval: Annotated[
            bool,
            Field(
                description=(
                    "Approve the request at once instead of sending it to the "
                    "approver. Only when the user asks for it."
                )
            ),
        ] = False,
    ) -> str:
        """Book time off in days for an employee. Sent once, never retried.

        HiBob checks the request against the policy and refuses, with its
        reason, a request that overlaps another, falls only on non-working
        days or lacks a required reason code. The new request is read back
        to report its status: "pending_approval" when the policy needs
        approval, else "approved".

        Returns:
            str: JSON {"employee", "request_id", "status", "request"}, with
            "readback_error" if it could not be read back, or an error
            beginning "Error:".
        """
        try:
            first = iso_date("start_date", start_date)
            last = iso_date("end_date", end_date) if end_date else first
            if last < first:
                raise ValueError(
                    f"end_date {last} is before start_date {first}. {NOTHING_WRITTEN}"
                )
            kind = str(policy_type or "").strip()
            if not kind:
                raise ValueError("policy_type must not be empty.")
            api = client_factory()
            person = await _person(api, employee)
            body: dict[str, Any] = {
                "policyType": kind,
                "requestRangeType": "days",
                "startDate": first,
                "endDate": last,
                "startDatePortion": start_portion,
                "endDatePortion": end_portion,
                "skipManagerApproval": skip_manager_approval,
            }
            if description:
                body["description"] = description
            if reason_code is not None:
                body["reasonCode"] = reason_code
            path = REQUESTS_PATH.format(employee_id=_segment(person["id"], "employee"))
            created = await api.post(path, body)
            new_id = (created or {}).get("id") if isinstance(created, dict) else None
            if new_id is None:
                raise ValueError(
                    "HiBob accepted the request but returned no request ID. Check "
                    "hibob_list_timeoff_requests before booking again."
                )
            rid = normalize_id(new_id)
            result: dict[str, Any] = {"employee": person, "request_id": rid}
            try:
                request = await _read_request(api, person, rid)
            except Exception as exc:
                result["readback_error"] = format_exception(exc)
                return _dump(result)
            result.update(status=request.get("status"), request=request)
            return _dump(result)
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_cancel_timeoff_request",
        annotations=ToolAnnotations(
            title="Cancel a HiBob time off request",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def hibob_cancel_timeoff_request(
        employee: Annotated[
            str,
            Field(
                description=EMPLOYEE_REF_DESCRIPTION + " The request must be theirs."
            ),
        ],
        request_id: Annotated[
            str, Field(description="The numeric time off request ID.")
        ],
        cancel_started: Annotated[
            bool,
            Field(
                description=(
                    "Cancel a request that started before today. Only after the "
                    "user confirms: the whole request goes, days already passed "
                    "included."
                )
            ),
        ] = False,
    ) -> str:
        """Cancel one time off request, pending or approved. Sent once.

        The request is read under the employee first: HiBob cancels by request
        ID alone, ignoring whose it is, so a request that is not this
        employee's is refused before anything is sent. A request already
        cancelled is reported without sending anything. A request that
        started before today is refused unless cancel_started is true. HiBob
        cancels at once, with no approval step; the request is read back to
        confirm it.

        Returns:
            str: JSON {"employee", "request_id", "cancelled", "previous_status",
            "status", "request"}, with "warning" if HiBob still shows it live,
            or an error beginning "Error:".
        """
        try:
            rid = _request_id(request_id)
            api = client_factory()
            person = await _person(api, employee)
            request = await _read_request(api, person, rid)
            status = request.get("status")
            result: dict[str, Any] = {
                "employee": person,
                "request_id": rid,
                "request": _request_summary(request),
            }
            if status == CANCELLED:
                result.update(
                    cancelled=False,
                    status=status,
                    note=f"The request was already cancelled. {NOTHING_WRITTEN}",
                )
                return _dump(result)
            if status not in CANCELLABLE:
                raise ValueError(
                    f"Request {rid} is {status!r}; only pending or approved requests "
                    f"can be cancelled. {NOTHING_WRITTEN}"
                )
            starts = _start(request)
            if starts is not None and starts < today() and not cancel_started:
                raise ValueError(
                    f"Request {rid} ({request.get('policyTypeDisplayName')}, "
                    f"{request.get('startDate')} to {request.get('endDate')}, "
                    f"{status}) started before today. HiBob would cancel the whole "
                    "request, days already passed included. Ask the user, then call "
                    f"again with cancel_started true. {NOTHING_WRITTEN}"
                )
            await api.delete(
                REQUEST_PATH.format(
                    employee_id=_segment(person["id"], "employee"), request_id=rid
                )
            )
            result["previous_status"] = status
            try:
                after = (await _read_request(api, person, rid)).get("status")
            except Exception as exc:
                result.update(cancelled=True, readback_error=format_exception(exc))
                return _dump(result)
            result.update(cancelled=after == CANCELLED, status=after)
            if after != CANCELLED:
                result["warning"] = (
                    f"HiBob accepted the cancellation but still shows the request "
                    f"as {after!r}."
                )
            return _dump(result)
        except Exception as exc:
            return format_exception(exc)
