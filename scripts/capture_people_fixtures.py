"""Capture HiBob people metadata and response shapes from the sandbox.

Read-only: an httpx request hook refuses every request except GETs and the
two read POSTs (people search, read one employee). Personal data in employee
responses is scrubbed before saving; metadata is saved as is.

Run: uv run --no-project --with httpx --with python-dotenv \
       python scripts/capture_people_fixtures.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

OUT = Path("tests/fixtures/people")
IDENTITY_FIELDS = ["root.id", "root.displayName", "root.email", "work.title"]
READ_FIELDS = [
    *IDENTITY_FIELDS,
    "root.firstName",
    "home.mobilePhone",
    "work.department",
    "work.site",
    "work.reportsTo",
    "work.startDate",
    "internal.status",
]
KEEP_KEYS = {
    "id",
    "effectiveDate",
    "endEffectiveDate",
    "activeEffectiveDate",
    "isCurrent",
    "canBeDeleted",
    "creationDate",
    "modificationDate",
    "workChangeType",
    "siteId",
    "currency",
    "payPeriod",
    "payFrequency",
    "contract",
    "type",
}
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def guard(request: httpx.Request) -> None:
    path = request.url.path
    if request.method == "GET":
        return
    if request.method == "POST" and (
        path == "/v1/people/search"
        or (path.startswith("/v1/people/") and path.count("/") == 3)
    ):
        return
    raise RuntimeError(f"blocked {request.method} {path}")


def scrub(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: scrub(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, key) for v in value[:3]]
    if isinstance(value, str):
        if key in KEEP_KEYS or _DATE.match(value):
            return value
        return "person@example.com" if "@" in value else "<text>"
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value if key in KEEP_KEYS else 1
    return value


def save(name: str, payload: Any) -> None:
    (OUT / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"saved {name}")


def main() -> None:
    env = dotenv_values(".env")
    host = (env.get("HIBOB_API_HOST") or "api.hibob.com").replace("https://", "")
    host = host.split("/")[0]
    client = httpx.Client(
        base_url=f"https://{host}/v1",
        auth=(
            env["HIBOB_SERVICE_USER_ID"] or "",
            env["HIBOB_SERVICE_USER_TOKEN"] or "",
        ),
        headers={"Accept": "application/json"},
        timeout=60,
        event_hooks={"request": [guard]},
    )
    OUT.mkdir(parents=True, exist_ok=True)
    notes: list[str] = [f"# People API observations ({host})", ""]

    fields = client.get("/company/people/fields")
    fields.raise_for_status()
    save("fields.json", fields.json())

    tables = client.get("/people/custom-tables/metadata")
    notes.append(f"- custom-tables metadata: {tables.status_code}")
    if tables.is_success:
        save("custom_tables.json", tables.json())

    search = client.post(
        "/people/search", json={"fields": IDENTITY_FIELDS, "humanReadable": "APPEND"}
    )
    notes.append(
        f"- people search without filters: {search.status_code} "
        f"{search.text[:200] if search.is_error else ''}"
    )
    search.raise_for_status()
    employees = search.json().get("employees", [])
    save("directory.json", scrub({"employees": employees[:3]}))
    employee_id = str(employees[0]["id"])

    read = client.post(
        f"/people/{employee_id}",
        json={"fields": READ_FIELDS, "humanReadable": "APPEND"},
    )
    keys = sorted(read.json())[:20] if read.is_success else read.text[:200]
    notes.append(f"- POST /people/{{id}}: {read.status_code}; top-level keys: {keys}")
    if read.is_success:
        save("employee_read.json", scrub(read.json()))

    for table in ("work", "employment", "salaries"):
        response = client.get(
            f"/people/{employee_id}/{table}", params={"includeHumanReadable": "true"}
        )
        notes.append(
            f"- GET /people/{{id}}/{table}: {response.status_code} "
            f"{response.headers.get('content-type')}"
        )
        if response.is_success and response.content:
            save(f"table_{table}.json", scrub(response.json()))

    for bulk in (
        "entitlement",
        "entitlements",
        "deduction",
        "deductions",
        "dependents",
        "right-to-work",
    ):
        response = client.get(
            f"/bulk/people/{bulk}", params={"employeeIds": employee_id, "limit": 1}
        )
        notes.append(
            f"- GET /bulk/people/{bulk}: {response.status_code} "
            f"{response.headers.get('content-type')}"
        )

    (OUT / "observations.md").write_text("\n".join(notes) + "\n")
    print("\n".join(notes))


if __name__ == "__main__":
    main()
