"""HiBob Tasks tools: read open tasks, find an employee, complete a task."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Annotated, Any, Literal
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .client import HiBobClient, get_client
from .errors import HiBobApiError, format_exception

TASKS_PATH = "/tasks"
EMPLOYEE_TASKS_PATH = "/tasks/people/{employee_id}"
COMPLETE_TASK_PATH = "/tasks/{task_id}/complete"
PEOPLE_SEARCH_PATH = "/people/search"


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


def _path_id(value: object, what: str) -> str:
    """A non-empty ID, URL-quoted for use as a path segment."""
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ValueError(f"{what} must not be empty.")
    return quote(text, safe="")


async def find_employees_by_email(client: HiBobClient, email: str) -> list[dict[str, Any]]:
    """Employees whose work email is ``email``: ID, display name and email."""
    body = {
        "fields": ["root.id", "root.displayName", "root.email"],
        "filters": [
            {"fieldPath": "root.email", "operator": "equals", "values": [email]}
        ],
    }
    try:
        payload = await client.search(PEOPLE_SEARCH_PATH, body)
    except HiBobApiError as exc:
        if exc.status_code == 403:
            raise HiBobApiError(
                "HiBob denied the employee lookup by email (403). The service "
                "user needs permission to read employees' names and email "
                "addresses.",
                status_code=403,
                hibob_key=exc.hibob_key,
                hibob_error=exc.hibob_error,
            ) from exc
        raise
    employees = payload.get("employees") if isinstance(payload, dict) else None
    return [
        {
            "id": str(e["id"]),
            "name": e.get("displayName"),
            "email": e.get("email"),
        }
        for e in employees or []
        if isinstance(e, dict) and e.get("id") is not None
    ]


def register_tasks_tools(
    mcp: FastMCP,
    *,
    read_only: bool = False,
    client_factory: Callable[[], HiBobClient] = get_client,
) -> None:
    """Register the tasks tools; the write tool is omitted when ``read_only``."""

    def client() -> HiBobClient:
        return client_factory()

    read_annotations = dict(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    )

    @mcp.tool(
        name="hibob_list_open_tasks",
        annotations=ToolAnnotations(title="List all open HiBob tasks", **read_annotations),
    )
    async def hibob_list_open_tasks() -> str:
        """List every open task in the company.

        Returns:
            str: JSON ``{"tasks": [...]}`` with each task's id, owner, title,
            requestedFor and so on, or an error beginning "Error:".

        HiBob returns at most 5,000 tasks, ordered by task ID, with no
        pagination: on a larger backlog the rest cannot be retrieved here. To
        see one person's tasks use hibob_get_employee_tasks instead.
        """
        try:
            return _dump(await client().get(TASKS_PATH))
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_find_employee",
        annotations=ToolAnnotations(title="Find a HiBob employee by email", **read_annotations),
    )
    async def hibob_find_employee(
        email: Annotated[str, Field(description="The employee's work email address.")],
    ) -> str:
        """Look up an employee by work email to get their employee ID.

        Returns:
            str: JSON ``{"count": N, "employees": [{"id", "name", "email"}]}``,
            or an error beginning "Error:". Names and emails often differ (for
            example after a name change), so check the name is the person meant
            before using the ID with hibob_get_employee_tasks.
        """
        try:
            address = email.strip().lower()
            if not address:
                raise ValueError("email must not be empty.")
            employees = await find_employees_by_email(client(), address)
            return _dump({"count": len(employees), "employees": employees})
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_get_employee_tasks",
        annotations=ToolAnnotations(title="Get a HiBob employee's tasks", **read_annotations),
    )
    async def hibob_get_employee_tasks(
        employee_id: Annotated[
            str, Field(description="The HiBob employee ID (see hibob_find_employee).")
        ],
        task_status: Annotated[
            Literal["open", "closed"] | None,
            Field(description="Only open or only closed tasks. Omit for both."),
        ] = None,
    ) -> str:
        """List the tasks of one employee.

        Returns:
            str: JSON ``{"tasks": [...]}``, or an error beginning "Error:".
        """
        try:
            path = EMPLOYEE_TASKS_PATH.format(
                employee_id=_path_id(employee_id, "employee_id")
            )
            if task_status:
                path += f"?task_status={task_status}"
            return _dump(await client().get(path))
        except Exception as exc:
            return format_exception(exc)

    if read_only:
        return

    @mcp.tool(
        name="hibob_complete_task",
        annotations=ToolAnnotations(
            title="Mark a HiBob task as complete",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def hibob_complete_task(
        task_id: Annotated[str, Field(description="The numeric ID of the task.")],
    ) -> str:
        """Mark one task as complete. Sent once, never retried.

        Returns:
            str: JSON ``{"task_id": ..., "completed": true}`` once HiBob
            accepts it, or an error beginning "Error:".
        """
        try:
            path = COMPLETE_TASK_PATH.format(task_id=_path_id(task_id, "task_id"))
            await client().post(path, {})
            return _dump({"task_id": str(task_id).strip(), "completed": True})
        except Exception as exc:
            return format_exception(exc)
