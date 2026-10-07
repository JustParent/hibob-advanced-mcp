"""HiBob employee-data reads: metadata, named lists, one employee, one table.

Field metadata, custom-table metadata and named lists change rarely and are
rate limited (50/min), so they are cached; employee data is never cached.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .cache import NamedListCache
from .client import HiBobClient
from .errors import HiBobApiError
from .list_values import named_list_items
from .people_fields import PeopleField, normalize_custom_tables, normalize_people_fields

FIELDS_METADATA_PATH = "/company/people/fields"
CUSTOM_TABLES_METADATA_PATH = "/people/custom-tables/metadata"
NAMED_LIST_PATH = "/company/named-lists/{name}"
EMPLOYEE_READ_PATH = "/people/{identifier}"
TABLE_PATH = "/people/{employee_id}/{table}"
CUSTOM_TABLE_PATH = "/people/custom-tables/{employee_id}/{table}"
# HiBob wants the string "true"; a boolean is answered with its login page.
HUMAN_READABLE_QUERY = "?includeHumanReadable=true"
# Tables readable one employee at a time, by the words a user would use.
HISTORY_TABLES = {
    "work": "work",
    "employment": "employment",
    "salary": "salaries",
    "salaries": "salaries",
    "lifecycle": "lifecycle",
    "variable pay": "variable",
    "variable": "variable",
    "equity": "equities",
    "equities": "equities",
    "training": "training",
    "bank account": "bank-accounts",
    "bank accounts": "bank-accounts",
    "bank-accounts": "bank-accounts",
}


async def people_fields(
    client: HiBobClient, cache: NamedListCache
) -> list[PeopleField]:
    async def fetch() -> list[PeopleField]:
        return normalize_people_fields(await client.get(FIELDS_METADATA_PATH))

    return await cache.get_or_fetch(("people:fields", False), fetch)


async def custom_tables(
    client: HiBobClient, cache: NamedListCache
) -> list[dict[str, Any]]:
    async def fetch() -> list[dict[str, Any]]:
        return normalize_custom_tables(await client.get(CUSTOM_TABLES_METADATA_PATH))

    return await cache.get_or_fetch(("people:custom-tables", False), fetch)


async def named_list(
    client: HiBobClient, cache: NamedListCache, list_id: str
) -> list[Any]:
    async def fetch() -> list[Any]:
        path = NAMED_LIST_PATH.format(name=quote(list_id, safe=""))
        return named_list_items(await client.get(path))

    return await cache.get_or_fetch((f"people:list:{list_id}", False), fetch)


def _one_employee(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict) and isinstance(payload.get("employees"), list):
        employees = payload["employees"]
        return employees[0] if employees and isinstance(employees[0], dict) else None
    if isinstance(payload, dict) and isinstance(payload.get("employee"), dict):
        return payload["employee"]
    return payload if isinstance(payload, dict) else None


async def read_employee(
    client: HiBobClient,
    identifier: str,
    fields: list[str],
    *,
    human_readable: bool = False,
) -> dict[str, Any] | None:
    """One employee's fields, by ID or work email; None if HiBob has no such
    employee. Finds inactive employees too."""
    body: dict[str, Any] = {"fields": fields}
    if human_readable:
        body["humanReadable"] = "APPEND"
    path = EMPLOYEE_READ_PATH.format(identifier=quote(identifier, safe="@"))
    try:
        payload = await client.search(path, body)
    except HiBobApiError as exc:
        if exc.status_code == 404:
            return None
        raise
    return _one_employee(payload)


async def read_table(
    client: HiBobClient, employee_id: str, table: str, *, custom: bool = False
) -> dict[str, Any]:
    """An employee's rows in one table, newest first, and any columns the
    service user may not see."""
    template = CUSTOM_TABLE_PATH if custom else TABLE_PATH
    path = template.format(
        employee_id=quote(employee_id, safe=""), table=quote(table, safe="")
    )
    payload = await client.get(path + HUMAN_READABLE_QUERY)
    rows: list[Any] = []
    restricted: dict[str, Any] = {}
    if isinstance(payload, dict):
        values = payload.get("values")
        rows = (
            [row for row in values if isinstance(row, dict)]
            if isinstance(values, list)
            else []
        )
        if isinstance(payload.get("restricted_columns"), dict):
            restricted = payload["restricted_columns"]
    rows.sort(key=lambda row: str(row.get("effectiveDate") or ""), reverse=True)
    return {"rows": rows, "restricted_columns": restricted}
