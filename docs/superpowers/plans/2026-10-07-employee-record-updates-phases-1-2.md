# Employee Record Updates (Phases 1–2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `hibob_list_employee_fields` and `hibob_get_employee` (phase 1) and `hibob_update_employee` for plain fields, work email and start date (phase 2) to the HiBob MCP server.

**Architecture:** Pure modules decide what a field is and how a value is written (`people_fields.py`, `employee_values.py`, `employee_rows.py`); thin async modules read HiBob with caching (`people_api.py`, `employee_directory.py`); the tools live in `employees.py` (reads, terminate) and `employee_updates.py` (update). Every check runs before the first write; each write is sent once and read back.

**Tech Stack:** Python ≥3.10, `mcp` FastMCP, `httpx`, `pydantic`, `pytest` + `pytest-asyncio` (auto mode) + `respx`, `ruff`, `mypy`.

**Spec:** `docs/superpowers/specs/2026-10-07-employee-record-updates-design.md`

**Not in this plan:** phase 3 (dated work/employment/salary rows) and phase 4 (`hibob_add_employee_record`, and listing its record types in `hibob_list_employee_fields`). Their code depends on facts marked **verify** in the spec (column IDs, `customColumns` nesting, bulk read paths), which Task 1 and Task 10 settle. Each gets its own plan afterwards.

## Global Constraints

- Python 3.10 and 3.12 both run in CI: no 3.11+ APIs (`datetime.UTC`, `tomllib`, `ExceptionGroup`).
- `ruff check .`, `ruff format --check .` and `mypy` must pass; run `ruff format .` before each commit.
- Tool results are JSON from `json.dumps(payload, indent=2, default=str)`, or a string beginning `Error:` from `errors.format_exception`.
- Writes are never retried; reads go through `client.get` / `client.search`, which retry 429/5xx only.
- Matching is exact, ignoring case. Ambiguity returns candidates; nothing is ever guessed.
- An effective date is never defaulted.
- `null` is refused as a value.
- `includeHumanReadable` is sent as the string `"true"`.
- No HiBob sandbox write without the user's explicit OK for that specific write, from a script that blocks any other write (memory: hibob-sandbox-writes-need-permission).
- Read tools are annotated `readOnlyHint=True, destructiveHint=False`; every tool has a `title` and a description longer than 40 characters (`tests/test_read_only_gating.py` enforces this).
- Test commands use the repo venv: `.venv/bin/pytest`, `.venv/bin/ruff`, `.venv/bin/mypy`.

## Review Focus

1. **Employee given as a JSON number** (`3332883884017713238` rather than a string): the tools must accept it and resolve the same employee. Pinned in Task 4 and Task 8.
2. **Display name with odd case or doubled spaces** (`"jane  SMITH"`): should match Jane Smith exactly. Pinned in Task 4.
3. **Multi-list value given as one comma-separated string** (`"Spanish, French"`): must come back as a question, not be stored as one wrong item. Pinned in Task 8.
4. **Email change to the same address in different case**: HiBob answers 304; the result must say nothing changed, not report an error or a success. Pinned in Task 9.
5. **A field label shared by two categories** (`Start date` in Work and in a Home custom field): must be a question naming both, and `Home > Start date` must pick one. Pinned in Task 3 and Task 8.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `scripts/capture_people_fixtures.py` (new) | Read-only sandbox capture of metadata and response shapes. |
| `tests/fixtures/people/*.json` (new) | Captured metadata and scrubbed response shapes. |
| `src/hibob_advanced_mcp/client.py` (modify) | Refuse an HTML body where JSON was expected. |
| `src/hibob_advanced_mcp/errors.py` (modify) | Permission, 400 and 404 messages for `/people/` paths. |
| `src/hibob_advanced_mcp/cache.py` (modify) | `get_or_fetch` helper. |
| `src/hibob_advanced_mcp/people_fields.py` (new, pure) | Field metadata, matching, routing, custom tables, reading a value out of a people record. |
| `src/hibob_advanced_mcp/people_api.py` (new) | Cached metadata and named-list reads; one employee's fields; one table. |
| `src/hibob_advanced_mcp/employee_directory.py` (new) | Resolve an employee by ID, email or name. |
| `src/hibob_advanced_mcp/employee_values.py` (new, pure) | Coerce a given value by field type; `NeedsInput`. |
| `src/hibob_advanced_mcp/employee_rows.py` (new, pure) | Nested `PUT /people` body; read-back comparison. |
| `src/hibob_advanced_mcp/employee_updates.py` (new) | `hibob_update_employee`. |
| `src/hibob_advanced_mcp/employees.py` (modify) | Register read tools, terminate, update tools. |
| `tests/people_data.py` (new) | Shared metadata, records and a fake HiBob people API for tool tests. |
| `tests/conftest.py`, `tests/test_read_only_gating.py`, `tests/test_stdio_server.py`, `README.md` (modify) | Registration, tool sets, counts, docs. |

---

### Task 1: Capture people metadata and response shapes from the sandbox (read-only)

The service-user token in `.env` returned 401 `tokenNotMatch` on 2026-10-07. This task needs a working token; ask the user to update `HIBOB_SERVICE_USER_TOKEN` in `.env` first. If they cannot yet, skip to Task 2 and come back: later tests use inline data shaped from the docs, and the fixture tests skip themselves until the files exist.

**Files:**
- Create: `scripts/capture_people_fixtures.py`
- Create: `tests/fixtures/people/fields.json`, `custom_tables.json`, `employee_read.json`, `directory.json`, `table_work.json`, `table_employment.json`, `table_salaries.json`, `observations.md`

**Interfaces:**
- Produces: fixture files read by `tests/test_people_fields.py::test_real_metadata_fixture` (Task 3) and `tests/test_employee_directory.py::test_real_read_fixture_gives_an_identity` (Task 4).

- [ ] **Step 1: Write the capture script**

```python
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
READ_FIELDS = IDENTITY_FIELDS + [
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
        f"- people search without filters: {search.status_code} {search.text[:200] if search.is_error else ''}"
    )
    search.raise_for_status()
    employees = search.json().get("employees", [])
    save("directory.json", scrub({"employees": employees[:3]}))
    employee_id = str(employees[0]["id"])

    read = client.post(
        f"/people/{employee_id}",
        json={"fields": READ_FIELDS, "humanReadable": "APPEND"},
    )
    notes.append(
        f"- POST /people/{{id}}: {read.status_code}; top-level keys: {sorted(read.json())[:20] if read.is_success else read.text[:200]}"
    )
    if read.is_success:
        save("employee_read.json", scrub(read.json()))

    for table in ("work", "employment", "salaries"):
        response = client.get(
            f"/people/{employee_id}/{table}", params={"includeHumanReadable": "true"}
        )
        notes.append(
            f"- GET /people/{{id}}/{table}: {response.status_code} {response.headers.get('content-type')}"
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
            f"- GET /bulk/people/{bulk}: {response.status_code} {response.headers.get('content-type')}"
        )

    (OUT / "observations.md").write_text("\n".join(notes) + "\n")
    print("\n".join(notes))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Dry-run the guard**

Run:

```bash
uv run --no-project --with httpx --with python-dotenv python - <<'EOF'
import importlib.util, httpx
spec = importlib.util.spec_from_file_location("c", "scripts/capture_people_fixtures.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
for method, path in [("PUT", "/v1/people/1"), ("POST", "/v1/people/1/work"), ("DELETE", "/v1/people/1")]:
    try:
        m.guard(httpx.Request(method, "https://x" + path)); print("NOT BLOCKED", method, path)
    except RuntimeError as e:
        print("blocked ok:", e)
m.guard(httpx.Request("POST", "https://x/v1/people/search")); print("search allowed")
EOF
```

Expected: three `blocked ok:` lines, then `search allowed`.

- [ ] **Step 3: Run the capture**

Run: `uv run --no-project --with httpx --with python-dotenv python scripts/capture_people_fixtures.py`
Expected: `saved fields.json` … and the observations printed. A 401 means the token is still wrong: stop and tell the user.

- [ ] **Step 4: Check the observations against this plan's assumptions**

Open `tests/fixtures/people/observations.md` and the fixtures, and check:
- `fields.json` is a list of objects with `id`, `name`, `categoryDisplayName`, `type`, `historical`, `jsonPath`. Note whether ids are `root.email` or `email` for root fields; Task 3's `canonical_field_id` handles both.
- People search without filters answered 200. **If it answered 400**, tell the user: Task 4's `directory()` needs a filter, and name lookups must be redesigned before Task 4.
- `employee_read.json`: slash keys (`"/work/title"`) or nested (`"work": {...}`), and where `humanReadable` sits. Task 3's `read_field` handles both shapes; if the read is wrapped (`{"employees": [...]}` or `{"employee": {...}}`), Task 4's `_one_employee` handles that.
- Which `/bulk/people/...` paths answered 200 (for phase 4).
Add a short summary of anything surprising to `observations.md`.

- [ ] **Step 5: Commit**

```bash
git add scripts/capture_people_fixtures.py tests/fixtures/people
git commit -m "Capture HiBob people metadata fixtures from the sandbox"
```

---

### Task 2: Client refuses HTML; errors explain employee-data failures

**Files:**
- Modify: `src/hibob_advanced_mcp/client.py` (`request`, imports)
- Modify: `src/hibob_advanced_mcp/errors.py` (`_permission_for`, 400 and 404 branches)
- Test: `tests/test_client.py`, `tests/test_errors.py`

**Interfaces:**
- Produces: `errors.PEOPLE_DATA_PERMISSION: str`; `HiBobClient.request` raises `HiBobApiError` for an HTML body.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_client.py`:

```python
async def test_html_page_in_place_of_json_is_an_error(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    """HiBob answers a wrong path or parameter with its login page and a 200."""
    mock_api.get("/people/1/variables").mock(
        return_value=httpx.Response(
            200,
            text="<!DOCTYPE html><html><body>Sign in</body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    with pytest.raises(HiBobApiError, match="HTML"):
        await client.get("/people/1/variables")
```

Append to `tests/test_errors.py` (and add `PEOPLE_DATA_PERMISSION` to its import from `hibob_advanced_mcp.errors`):

```python
PEOPLE_URL = "https://api.hibob.com/v1/people/3332883884017713238"


def test_403_on_employee_data_names_the_people_fields_permission() -> None:
    with pytest.raises(HiBobApiError) as excinfo:
        raise_for_hibob_error(_response(403, {}, url=f"{PEOPLE_URL}/work"))
    assert PEOPLE_DATA_PERMISSION in str(excinfo.value)
    assert "Manage positions" not in str(excinfo.value)


def test_404_on_employee_data_points_at_find_employee() -> None:
    with pytest.raises(HiBobApiError) as excinfo:
        raise_for_hibob_error(_response(404, {}, url=PEOPLE_URL))
    assert "hibob_find_employee" in str(excinfo.value)


def test_400_on_employee_data_points_at_the_employee_fields_tool() -> None:
    body = {"key": "x", "error": "Unknown field ID: /work/site"}
    with pytest.raises(HiBobApiError) as excinfo:
        raise_for_hibob_error(_response(400, body, url=PEOPLE_URL))
    message = str(excinfo.value)
    assert "Unknown field ID: /work/site" in message
    assert "hibob_list_employee_fields" in message
    assert "hibob_list_workforce_fields" not in message
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_client.py tests/test_errors.py -q`
Expected: 4 failures (`ImportError` for `PEOPLE_DATA_PERMISSION` fails the errors module; the client test fails with `DID NOT RAISE`).

- [ ] **Step 3: Implement**

In `src/hibob_advanced_mcp/client.py`, change the errors import and `request`, and add `_is_html`:

```python
from .errors import HiBobApiError, HiBobConfigError, raise_for_hibob_error
```

```python
    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        is_read: bool = False,
    ) -> Any:
        """Send a request and return the decoded JSON body.

        Returns ``None`` for empty bodies (HiBob answers some writes with 204).
        An HTML body is refused: HiBob answers a wrong path or parameter with
        its login page and a 200.
        """
        response = await self.request_response(method, path, json=json, is_read=is_read)
        if response.status_code == 204 or not response.content:
            return None
        if _is_html(response):
            raise HiBobApiError(
                f"HiBob answered {method} {path} with an HTML page rather than "
                "JSON, which it does when the endpoint path or a parameter is "
                "wrong.",
                status_code=response.status_code,
            )
        try:
            return response.json()
        except ValueError:
            return response.text
```

```python
def _is_html(response: httpx.Response) -> bool:
    if "text/html" in response.headers.get("content-type", "").lower():
        return True
    head = response.content[:64].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")
```

(Place `_is_html` above `class HiBobClient`.)

In `src/hibob_advanced_mcp/errors.py`, below `EMPLOYEE_LIFECYCLE_PERMISSION`:

```python
PEOPLE_DATA_PERMISSION = (
    "People's data > People's fields: View on the categories read, Edit on "
    "those changed, and View history to read a table's earlier rows"
)
```

In `_permission_for`, after the `"/employees/"` branch:

```python
    if "/people/" in path:
        return PEOPLE_DATA_PERMISSION
```

Change the employees 404 branch condition to cover both paths:

```python
    elif status == 404 and ("/employees/" in path or "/people/" in path):
```

Add a 400 branch directly after the termination 400 branch:

```python
    elif status == 400 and "/people/" in path:
        message = (
            "HiBob rejected the employee change (400)"
            + (f": {detail.rstrip('.')}." if detail else ".")
            + " Check field IDs and values with hibob_list_employee_fields."
        )
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_client.py tests/test_errors.py tests/test_tools_employees.py tests/test_tools_tasks.py -q`
Expected: all pass (the tasks 404 branch still precedes the people branch, so `/tasks/people/...` keeps its message).

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add src/hibob_advanced_mcp/client.py src/hibob_advanced_mcp/errors.py tests/test_client.py tests/test_errors.py
git commit -m "Refuse HTML responses and explain employee-data errors"
```

---

### Task 3: `people_fields.py` — field metadata, matching, routing, reading values

**Files:**
- Create: `src/hibob_advanced_mcp/people_fields.py`
- Create: `tests/people_data.py` (metadata part)
- Test: `tests/test_people_fields.py`

**Interfaces:**
- Produces:
  - `ROOT_PREFIX = "root."`, `EMAIL_FIELD = "root.email"`, `START_DATE_FIELD = "work.startDate"`
  - `@dataclass(frozen=True) class PeopleField(id: str, label: str, category: str, category_id: str, type: str, list_id: str | None, json_path: str, historical: bool, calculated: bool)` with property `qualified_label -> str` (`"Work > Job title"`)
  - `@dataclass(frozen=True) class Route(kind: Literal["field","dated","email","start_date","not_writable"], table: str | None = None, reason: str | None = None)`
  - `canonical_field_id(text: Any) -> str`
  - `normalize_people_fields(payload: Any) -> list[PeopleField]`
  - `find_fields(fields: list[PeopleField], text: Any) -> list[PeopleField]`
  - `nearest_fields(fields: list[PeopleField], text: Any, limit: int = 5) -> list[PeopleField]`
  - `route_for(field: PeopleField) -> Route`
  - `describe_field(field: PeopleField) -> dict[str, Any]`
  - `normalize_custom_tables(payload: Any) -> list[dict[str, Any]]`
  - `read_field(record: Any, field_id: str, json_path: str | None = None) -> tuple[Any, Any]` → `(value, display)`
  - `people_data.FIELDS`, `people_data.CUSTOM_TABLES` (test data)

- [ ] **Step 1: Write the shared test data**

Create `tests/people_data.py`:

```python
"""Employee metadata and records shaped like HiBob's, for the employee tools."""

from __future__ import annotations

from typing import Any


def _field(
    field_id: str,
    name: str,
    category: str,
    field_type: str,
    *,
    list_id: str | None = None,
    historical: bool = False,
    calculated: bool = False,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": field_id,
        "name": name,
        "categoryId": field_id.split(".")[0],
        "categoryDisplayName": category,
        "type": field_type,
        "jsonPath": field_id,
        "historical": historical,
    }
    if list_id:
        entry["typeData"] = {"listId": list_id}
    if calculated:
        entry["calculated"] = True
    return entry


FIELDS = [
    _field("root.id", "Employee ID", "Basic info", "text", calculated=True),
    _field("root.displayName", "Display name", "Basic info", "text", calculated=True),
    _field("root.email", "Email", "Basic info", "text"),
    _field("root.firstName", "First name", "Basic info", "text"),
    _field("home.mobilePhone", "Mobile phone", "Home", "text"),
    _field("work.title", "Job title", "Work", "list", list_id="title", historical=True),
    _field(
        "work.department",
        "Department",
        "Work",
        "list",
        list_id="department",
        historical=True,
    ),
    _field("work.site", "Site", "Work", "list", list_id="site", historical=True),
    _field(
        "work.reportsTo", "Reports to", "Work", "employee-reference", historical=True
    ),
    _field("work.startDate", "Start date", "Work", "date"),
    _field("internal.status", "Status", "Internal", "list", list_id="status"),
    _field("address.city", "City", "Address", "text", historical=True),
    _field(
        "payroll.salary.payment", "Base salary", "Payroll", "currency", historical=True
    ),
    _field("work.custom.field_100", "Shirt size", "Work", "list", list_id="shirtSize"),
    _field("home.custom.field_200", "Start date", "Home", "date"),
    _field(
        "about.custom.field_300",
        "Languages",
        "About",
        "multi-list",
        list_id="languages",
    ),
    _field("work.custom.field_400", "Buddy", "Work", "employee-reference"),
    _field("financial.custom.field_500", "Bonus target", "Financial", "currency"),
    _field("personal.custom.field_600", "Passport scan", "Personal", "document"),
    _field("about.custom.field_700", "Remote", "About", "boolean"),
    _field("about.custom.field_800", "Desk number", "About", "number"),
]

CUSTOM_TABLES = {
    "tables": [
        {
            "id": "about__table_1",
            "name": "Certifications",
            "category": "about",
            "columns": [
                {
                    "id": "column_1",
                    "name": "Certificate",
                    "type": "list",
                    "typeData": {"listId": "certs"},
                    "mandatory": True,
                },
                {
                    "id": "column_2",
                    "name": "Expires",
                    "type": "date",
                    "mandatory": False,
                },
            ],
        }
    ]
}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_people_fields.py`:

```python
"""people_fields: what an employee field is and how a change to it is written."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hibob_advanced_mcp.people_fields import (
    canonical_field_id,
    describe_field,
    find_fields,
    nearest_fields,
    normalize_custom_tables,
    normalize_people_fields,
    read_field,
    route_for,
)
from people_data import CUSTOM_TABLES, FIELDS

ALL = normalize_people_fields(FIELDS)
BY_ID = {field.id: field for field in ALL}
REAL_FIELDS = Path(__file__).parent / "fixtures" / "people" / "fields.json"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("work.title", "work.title"),
        ("/work/title", "work.title"),
        ("firstName", "root.firstName"),
        ("/root/email", "root.email"),
        (" root.id ", "root.id"),
    ],
)
def test_canonical_field_id(given: str, expected: str) -> None:
    assert canonical_field_id(given) == expected


def test_normalize_reads_label_category_list_and_flags() -> None:
    title = BY_ID["work.title"]
    assert (title.label, title.category, title.type, title.list_id) == (
        "Job title",
        "Work",
        "list",
        "title",
    )
    assert title.historical is True
    assert title.qualified_label == "Work > Job title"
    assert BY_ID["root.displayName"].calculated is True


def test_normalize_accepts_a_wrapped_list_and_skips_junk() -> None:
    fields = normalize_people_fields({"fields": [FIELDS[2], "junk", {"name": "no id"}]})
    assert [field.id for field in fields] == ["root.email"]


def test_find_by_id_in_any_spelling_or_by_label_ignoring_case() -> None:
    assert [f.id for f in find_fields(ALL, "/work/title")] == ["work.title"]
    assert [f.id for f in find_fields(ALL, "JOB TITLE")] == ["work.title"]
    assert [f.id for f in find_fields(ALL, "email")] == ["root.email"]


def test_a_shared_label_returns_both_and_a_qualified_label_picks_one() -> None:
    assert {f.id for f in find_fields(ALL, "Start date")} == {
        "work.startDate",
        "home.custom.field_200",
    }
    assert [f.id for f in find_fields(ALL, "home > start date")] == [
        "home.custom.field_200"
    ]


def test_find_refuses_an_empty_name() -> None:
    with pytest.raises(ValueError):
        find_fields(ALL, "  ")


def test_nearest_fields_offers_labels_sharing_a_word() -> None:
    assert "work.title" in [f.id for f in nearest_fields(ALL, "title")]


@pytest.mark.parametrize(
    ("field_id", "kind", "table"),
    [
        ("home.mobilePhone", "field", None),
        ("work.custom.field_100", "field", None),
        ("root.email", "email", None),
        ("work.startDate", "start_date", None),
        ("work.title", "dated", "work"),
        ("work.reportsTo", "dated", "work"),
        ("payroll.salary.payment", "dated", "salary"),
        ("address.city", "not_writable", None),
        ("internal.status", "not_writable", None),
        ("root.displayName", "not_writable", None),
        ("personal.custom.field_600", "not_writable", None),
    ],
)
def test_route_for(field_id: str, kind: str, table: str | None) -> None:
    route = route_for(BY_ID[field_id])
    assert (route.kind, route.table) == (kind, table)
    assert bool(route.reason) is (kind == "not_writable")


def test_describe_field_says_how_it_is_written() -> None:
    assert describe_field(BY_ID["work.department"]) == {
        "id": "work.department",
        "label": "Department",
        "category": "Work",
        "type": "list",
        "write": "dated",
        "list": "department",
        "table": "work",
    }
    assert describe_field(BY_ID["address.city"])["reason"]


def test_custom_tables_keep_columns_and_required_flags() -> None:
    assert normalize_custom_tables(CUSTOM_TABLES) == [
        {
            "id": "about__table_1",
            "name": "Certifications",
            "category": "about",
            "columns": [
                {
                    "id": "column_1",
                    "label": "Certificate",
                    "type": "list",
                    "required": True,
                    "list": "certs",
                },
                {
                    "id": "column_2",
                    "label": "Expires",
                    "type": "date",
                    "required": False,
                },
            ],
        }
    ]


def test_read_field_from_a_nested_record_with_display_labels() -> None:
    record = {
        "email": "jane@x.com",
        "work": {"title": "101"},
        "humanReadable": {"work": {"title": "Analyst"}},
    }
    assert read_field(record, "work.title") == ("101", "Analyst")
    assert read_field(record, "root.email") == ("jane@x.com", None)


def test_read_field_from_slash_keys() -> None:
    record = {
        "/work/title": {"value": "101", "humanReadable": "Analyst"},
        "/root/id": {"value": "7"},
    }
    assert read_field(record, "work.title") == ("101", "Analyst")
    assert read_field(record, "root.id") == ("7", None)


def test_read_field_missing_or_not_a_record() -> None:
    assert read_field({}, "work.title") == (None, None)
    assert read_field(None, "work.title") == (None, None)


@pytest.mark.skipif(not REAL_FIELDS.exists(), reason="sandbox fixture not captured")
def test_real_metadata_fixture() -> None:
    fields = {
        f.id: f for f in normalize_people_fields(json.loads(REAL_FIELDS.read_text()))
    }
    assert route_for(fields["work.title"]).table == "work"
    assert route_for(fields["root.email"]).kind == "email"
    assert all(f.label for f in fields.values())
```

- [ ] **Step 3: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_people_fields.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'hibob_advanced_mcp.people_fields'`.

- [ ] **Step 4: Implement `people_fields.py`**

```python
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

from dataclasses import dataclass
from typing import Any, Literal

from .forms import rank_matches

ROOT_PREFIX = "root."
EMAIL_FIELD = "root.email"
START_DATE_FIELD = "work.startDate"
MAX_CANDIDATES = 5
# Effective-dated tables the API can add rows to, by field ID prefix.
DATED_TABLE_PREFIXES = (
    ("payroll.employment.", "employment"),
    ("payroll.salary.", "salary"),
    ("work.", "work"),
)
NOT_WRITABLE_TYPES = frozenset({"document"})
# HiBob calculates these; its metadata does not always say so.
CALCULATED_FIELDS = frozenset(
    {
        "root.fullName",
        "work.activeEffectiveDate",
        "payroll.employment.activeEffectiveDate",
    }
)

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
    fields: list[PeopleField] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        field_id = canonical_field_id(item["id"])
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
        return Route("not_writable", reason="documents cannot be set through the API")
    if field.id.startswith("internal."):
        return Route(
            "not_writable",
            reason="HiBob manages it; lifecycle changes have their own tools, "
            "such as hibob_terminate_employee",
        )
    if field.historical:
        for prefix, table in DATED_TABLE_PREFIXES:
            if field.id.startswith(prefix):
                return Route("dated", table=table)
        if field.id.startswith("address."):
            return Route("not_writable", reason="HiBob's API cannot change an address")
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
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_people_fields.py -q`
Expected: all pass (`test_real_metadata_fixture` passes if Task 1 ran, else skipped).

- [ ] **Step 6: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add src/hibob_advanced_mcp/people_fields.py tests/people_data.py tests/test_people_fields.py
git commit -m "Add employee field metadata, matching and routing"
```

---

### Task 4: `people_api.py` and `employee_directory.py` — cached reads and employee lookup

**Files:**
- Modify: `src/hibob_advanced_mcp/cache.py` (add `get_or_fetch`)
- Create: `src/hibob_advanced_mcp/people_api.py`
- Create: `src/hibob_advanced_mcp/employee_directory.py`
- Modify: `tests/people_data.py` (records, lists, fake API)
- Test: `tests/test_employee_directory.py`

**Interfaces:**
- Consumes: `people_fields.normalize_people_fields`, `normalize_custom_tables`, `read_field`, `PeopleField`; `list_values.named_list_items`; `forms.rank_matches`; `envelopes.normalize_id`.
- Produces:
  - `NamedListCache.get_or_fetch(key: tuple[str, bool], fetch: Callable[[], Awaitable[Any]]) -> Any` (async)
  - `people_api.people_fields(client, cache) -> list[PeopleField]`
  - `people_api.custom_tables(client, cache) -> list[dict[str, Any]]`
  - `people_api.named_list(client, cache, list_id: str) -> list[Any]`
  - `people_api.read_employee(client, identifier: str, fields: list[str], *, human_readable: bool = False) -> dict[str, Any] | None` (None on 404)
  - `people_api.read_table(client, employee_id: str, table: str, *, custom: bool = False) -> dict[str, Any]` → `{"rows": [...], "restricted_columns": {...}}`, rows newest first
  - `people_api.HISTORY_TABLES: dict[str, str]` (user word → path segment)
  - `employee_directory.EmployeeMatch(employee: dict | None, candidates: list[dict], ambiguous: bool = False)`
  - `employee_directory.IDENTITY_FIELDS: list[str]`
  - `employee_directory.find_employee(client, cache, ref: str | int) -> EmployeeMatch`
  - `employee_directory.describe_candidates(candidates: list[dict]) -> str`
  - Identity dicts are `{"id": str, "name": str | None, "email": str | None, "title"?: str}`
  - `people_data.EMPLOYEE_ID`, `MANAGER_ID`, `JANE`, `SAM`, `LISTS`, `FakePeople(mock_api)` with `.writes: list[str]`, `.records: dict[str, dict]`

- [ ] **Step 1: Add records, lists and the fake API to `tests/people_data.py`**

Append:

```python
import copy
import json as jsonlib

import httpx
import respx

EMPLOYEE_ID = "3332883884017713238"
MANAGER_ID = "3332883884017713999"

JANE: dict[str, Any] = {
    "id": EMPLOYEE_ID,
    "displayName": "Jane Smith",
    "email": "jane@x.com",
    "firstName": "Jane",
    "home": {"mobilePhone": "07700 900000"},
    "work": {
        "title": "101",
        "department": "201",
        "startDate": "2024-03-01",
        "reportsTo": {"id": MANAGER_ID, "email": "sam@x.com"},
        "custom": {"field_100": "L"},
    },
    "about": {"custom": {"field_300": ["1"], "field_700": False}},
    "internal": {"status": "Active"},
    "humanReadable": {
        "work": {
            "title": "Analyst",
            "department": "Data",
            "reportsTo": "Sam Jones",
            "custom": {"field_100": "Large"},
        }
    },
}
SAM: dict[str, Any] = {
    "id": MANAGER_ID,
    "displayName": "Sam Jones",
    "email": "sam@x.com",
    "work": {"title": "102"},
    "humanReadable": {"work": {"title": "Head of Data"}},
}
DIRECTORY = {
    "employees": [
        JANE,
        SAM,
        {"id": "77", "displayName": "Alex Lee", "email": "alex.lee@x.com"},
        {"id": "78", "displayName": "Alex Lee", "email": "alex.lee2@x.com"},
    ]
}
LISTS: dict[str, dict[str, Any]] = {
    "department": {
        "name": "department",
        "values": [
            {"id": "201", "name": "Data", "value": "Data"},
            {"id": "202", "name": "Marketing", "value": "Marketing"},
        ],
    },
    "shirtSize": {
        "name": "shirtSize",
        "values": [
            {"id": "M", "name": "Medium", "value": "Medium"},
            {"id": "L", "name": "Large", "value": "Large"},
            {"id": "L2", "name": "Large", "value": "Large"},
        ],
    },
    "languages": {
        "name": "languages",
        "values": [
            {"id": "1", "name": "Spanish", "value": "Spanish"},
            {"id": "2", "name": "French", "value": "French"},
        ],
    },
}


def _merge(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


class FakePeople:
    """HiBob's people API, enough for the employee tools.

    Reads answer from ``records``; a PUT /people/{id} merges its body into the
    record unless ``apply_writes`` is false (as HiBob does with fields the
    service user may not edit). Every write is appended to ``writes``.
    """

    def __init__(self, mock_api: respx.MockRouter) -> None:
        self.records = {
            EMPLOYEE_ID: copy.deepcopy(JANE),
            MANAGER_ID: copy.deepcopy(SAM),
        }
        self.writes: list[str] = []
        self.apply_writes = True
        self.put_status = 200
        mock_api.get("/company/people/fields").mock(
            return_value=httpx.Response(200, json=FIELDS)
        )
        mock_api.get("/people/custom-tables/metadata").mock(
            return_value=httpx.Response(200, json=CUSTOM_TABLES)
        )
        self.directory = mock_api.post("/people/search").mock(
            return_value=httpx.Response(200, json=DIRECTORY)
        )
        for name, body in LISTS.items():
            mock_api.get(f"/company/named-lists/{name}").mock(
                return_value=httpx.Response(200, json=body)
            )
        for identifier in (EMPLOYEE_ID, MANAGER_ID, "jane@x.com", "sam@x.com"):
            mock_api.post(f"/people/{identifier}").mock(side_effect=self._read)
        mock_api.post("/people/nobody@x.com").mock(
            return_value=httpx.Response(404, json={})
        )
        self.put = mock_api.put(f"/people/{EMPLOYEE_ID}").mock(side_effect=self._put)

    def _record(self, identifier: str) -> dict[str, Any] | None:
        for record in self.records.values():
            if identifier in (record["id"], record["email"]):
                return record
        return None

    def _read(self, request: httpx.Request) -> httpx.Response:
        record = self._record(request.url.path.rsplit("/", 1)[-1])
        if record is None:
            return httpx.Response(404, json={})
        return httpx.Response(200, json=record)

    def _put(self, request: httpx.Request) -> httpx.Response:
        self.writes.append("fields")
        if self.put_status != 200:
            return httpx.Response(self.put_status)
        if self.apply_writes:
            _merge(self.records[EMPLOYEE_ID], jsonlib.loads(request.content))
        return httpx.Response(200)
```

(Move the new imports to the top of the file with the existing `from typing import Any`.)

- [ ] **Step 2: Write the failing tests**

Create `tests/test_employee_directory.py`:

```python
"""Employee lookup by ID, email or name, and the cached people reads."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from hibob_advanced_mcp.cache import NamedListCache
from hibob_advanced_mcp.client import HiBobClient
from hibob_advanced_mcp.employee_directory import (
    describe_candidates,
    find_employee,
    identity,
)
from hibob_advanced_mcp.people_api import people_fields, read_employee, read_table
from people_data import EMPLOYEE_ID, MANAGER_ID, FakePeople

REAL_READ = Path(__file__).parent / "fixtures" / "people" / "employee_read.json"


async def test_get_or_fetch_fetches_once() -> None:
    cache = NamedListCache()
    calls = []

    async def fetch() -> list[int]:
        calls.append(1)
        return [1]

    assert await cache.get_or_fetch(("k", False), fetch) == [1]
    assert await cache.get_or_fetch(("k", False), fetch) == [1]
    assert len(calls) == 1


async def test_find_by_email_reads_that_employee(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    FakePeople(mock_api)
    match = await find_employee(client, NamedListCache(), " Jane@X.com ")
    assert match.employee == {
        "id": EMPLOYEE_ID,
        "name": "Jane Smith",
        "email": "jane@x.com",
        "title": "Analyst",
    }


async def test_find_by_id_given_as_a_number(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    FakePeople(mock_api)
    match = await find_employee(client, NamedListCache(), int(MANAGER_ID))
    assert match.employee is not None
    assert match.employee["name"] == "Sam Jones"


async def test_find_by_name_ignores_case_and_repeated_spaces(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    FakePeople(mock_api)
    match = await find_employee(client, NamedListCache(), "jane  SMITH")
    assert match.employee is not None
    assert match.employee["id"] == EMPLOYEE_ID


async def test_a_shared_name_is_ambiguous_with_both_candidates(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    FakePeople(mock_api)
    match = await find_employee(client, NamedListCache(), "Alex Lee")
    assert match.employee is None
    assert match.ambiguous is True
    assert {c["email"] for c in match.candidates} == {
        "alex.lee@x.com",
        "alex.lee2@x.com",
    }
    assert "alex.lee2@x.com" in describe_candidates(match.candidates)


async def test_unknown_email_or_name_finds_nobody(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    FakePeople(mock_api)
    cache = NamedListCache()
    assert (await find_employee(client, cache, "nobody@x.com")).employee is None
    match = await find_employee(client, cache, "Zed Zebra")
    assert match.employee is None
    assert match.ambiguous is False


async def test_the_directory_is_fetched_once_across_lookups(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    fake = FakePeople(mock_api)
    cache = NamedListCache()
    await find_employee(client, cache, "Jane Smith")
    await find_employee(client, cache, "Sam Jones")
    assert fake.directory.call_count == 1


async def test_people_fields_are_cached(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    FakePeople(mock_api)
    cache = NamedListCache()
    await people_fields(client, cache)
    fields = await people_fields(client, cache)
    assert mock_api.calls.call_count == 1
    assert any(f.id == "work.title" for f in fields)


async def test_read_employee_unwraps_a_list_response(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    mock_api.post("/people/9").mock(
        return_value=httpx.Response(200, json={"employees": [{"id": "9"}]})
    )
    assert await read_employee(client, "9", ["root.id"]) == {"id": "9"}


async def test_read_table_sends_human_readable_as_a_string_and_sorts_newest_first(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get(f"/people/{EMPLOYEE_ID}/work").mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {"id": 1, "effectiveDate": "2024-03-01"},
                    {"id": 2, "effectiveDate": "2025-01-01"},
                ],
                "restricted_columns": {"no_view_permission": ["salary"]},
            },
        )
    )
    table = await read_table(client, EMPLOYEE_ID, "work")
    assert route.calls.last.request.url.params["includeHumanReadable"] == "true"
    assert [row["id"] for row in table["rows"]] == [2, 1]
    assert table["restricted_columns"] == {"no_view_permission": ["salary"]}


async def test_read_table_with_an_empty_body_has_no_rows(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/people/custom-tables/7/about__table_1").mock(
        return_value=httpx.Response(200)
    )
    table = await read_table(client, "7", "about__table_1", custom=True)
    assert table == {"rows": [], "restricted_columns": {}}


def test_real_read_fixture_gives_an_identity() -> None:
    if not REAL_READ.exists():
        return
    found = identity(json.loads(REAL_READ.read_text()))
    assert found is not None and found["id"]
```

- [ ] **Step 3: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_employee_directory.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'hibob_advanced_mcp.employee_directory'`.

- [ ] **Step 4: Implement**

Add to `NamedListCache` in `src/hibob_advanced_mcp/cache.py` (and `from collections.abc import Awaitable, Callable`):

```python
async def get_or_fetch(self, key: CacheKey, fetch: Callable[[], Awaitable[Any]]) -> Any:
    """The cached value for ``key``, fetching and caching it if absent."""
    hit = self.get(key)
    if hit is not None:
        return hit
    value = await fetch()
    self.set(key, value)
    return value
```

Create `src/hibob_advanced_mcp/people_api.py`:

```python
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
```

Create `src/hibob_advanced_mcp/employee_directory.py`:

```python
"""Finding the employee a user means: by ID, work email or display name.

An ID or email is read directly (POST /people/{identifier}), which finds
inactive employees too. A name is matched against a directory of active
employees, exactly but ignoring case and repeated spaces; several people
sharing it are returned as candidates rather than one being picked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .cache import NamedListCache
from .client import HiBobClient
from .envelopes import normalize_id
from .forms import rank_matches
from .people_api import read_employee
from .people_fields import read_field

PEOPLE_SEARCH_PATH = "/people/search"
IDENTITY_FIELDS = ["root.id", "root.displayName", "root.email", "work.title"]
MAX_CANDIDATES = 5


@dataclass(frozen=True)
class EmployeeMatch:
    employee: dict[str, Any] | None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: bool = False


def identity(record: Any) -> dict[str, Any] | None:
    """ID, name, email and title from a people record."""
    employee_id, _ = read_field(record, "root.id")
    if employee_id in (None, ""):
        return None
    name, _ = read_field(record, "root.displayName")
    email, _ = read_field(record, "root.email")
    title, title_display = read_field(record, "work.title")
    found: dict[str, Any] = {
        "id": normalize_id(employee_id),
        "name": name,
        "email": email,
    }
    if title_display or title:
        found["title"] = title_display or title
    return found


def describe_candidates(candidates: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{c.get('name') or c['id']} ({c.get('email') or 'no email'}, ID {c['id']})"
        for c in candidates
    )


def _squash(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


async def _directory(
    client: HiBobClient, cache: NamedListCache
) -> list[dict[str, Any]]:
    async def fetch() -> list[dict[str, Any]]:
        payload = await client.search(
            PEOPLE_SEARCH_PATH, {"fields": IDENTITY_FIELDS, "humanReadable": "APPEND"}
        )
        employees = payload.get("employees") if isinstance(payload, dict) else None
        found = [identity(e) for e in employees or []]
        return [person for person in found if person]

    return await cache.get_or_fetch(("people:directory", False), fetch)


async def find_employee(
    client: HiBobClient, cache: NamedListCache, ref: str | int
) -> EmployeeMatch:
    text = str(ref).strip() if ref is not None else ""
    if not text:
        raise ValueError("employee must not be empty.")
    if "@" in text or text.isdigit():
        record = await read_employee(
            client,
            text.lower() if "@" in text else text,
            IDENTITY_FIELDS,
            human_readable=True,
        )
        return EmployeeMatch(identity(record) if record else None)
    people = await _directory(client, cache)
    wanted = _squash(text)
    exact = [person for person in people if _squash(person.get("name")) == wanted]
    if len(exact) == 1:
        return EmployeeMatch(exact[0])
    if exact:
        return EmployeeMatch(None, exact, ambiguous=True)
    by_id = {person["id"]: person for person in people}
    leaves = [
        {"id": person["id"], "name": person.get("name") or ""} for person in people
    ]
    near = rank_matches(leaves, text, require_all=True)[:MAX_CANDIDATES]
    return EmployeeMatch(None, [by_id[leaf["id"]] for leaf in near])
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_employee_directory.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add src/hibob_advanced_mcp/cache.py src/hibob_advanced_mcp/people_api.py src/hibob_advanced_mcp/employee_directory.py tests/people_data.py tests/test_employee_directory.py
git commit -m "Add cached people reads and employee lookup by ID, email or name"
```

---

### Task 5: `hibob_list_employee_fields`

**Files:**
- Modify: `src/hibob_advanced_mcp/employees.py`
- Modify: `tests/conftest.py`, `tests/test_read_only_gating.py`, `tests/test_stdio_server.py`, `README.md`
- Test: `tests/test_tools_employee_reads.py`

**Interfaces:**
- Consumes: `people_api.people_fields`, `people_api.custom_tables`, `people_fields.describe_field`, `NamedListCache`.
- Produces: `register_employee_tools(mcp, *, read_only=False, client_factory=get_client, list_cache: NamedListCache | None = None)`; read tools registered even when `read_only`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tools_employee_reads.py`:

```python
"""hibob_list_employee_fields and hibob_get_employee."""

from __future__ import annotations

import json

import httpx

from conftest import call_tool
from people_data import FakePeople


async def test_list_fields_says_how_each_is_written(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    result = json.loads(await call_tool(mcp_server, "hibob_list_employee_fields", {}))
    by_id = {field["id"]: field for field in result["fields"]}
    assert by_id["work.title"]["write"] == "dated"
    assert by_id["work.title"]["table"] == "work"
    assert by_id["home.mobilePhone"]["write"] == "field"
    assert by_id["root.email"]["write"] == "email"
    assert by_id["address.city"]["write"] == "not_writable"
    assert result["custom_tables"][0]["name"] == "Certifications"


async def test_list_fields_search_narrows_by_label_id_or_category(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await call_tool(mcp_server, "hibob_list_employee_fields", {"search": "start"})
    )
    assert {field["id"] for field in result["fields"]} == {
        "work.startDate",
        "home.custom.field_200",
    }
    assert result["count"] == 2
    assert result["custom_tables"] == []


async def test_list_fields_survives_custom_tables_being_denied(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    mock_api.get("/people/custom-tables/metadata").mock(
        return_value=httpx.Response(403, json={})
    )
    result = json.loads(await call_tool(mcp_server, "hibob_list_employee_fields", {}))
    assert result["fields"]
    assert "403" in result["custom_tables_error"]


async def test_list_fields_is_available_in_read_only_mode(
    mock_api, server_factory
) -> None:
    FakePeople(mock_api)
    text = await call_tool(
        server_factory(read_only=True), "hibob_list_employee_fields", {}
    )
    assert not text.startswith("Error:")
```

Edit `tests/test_read_only_gating.py`: add `"hibob_list_employee_fields",` to `READ_TOOLS`.
Edit `tests/test_stdio_server.py`: `== 29` → `== 30` (both places) and the read-only `== 18` → `== 19`.

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_tools_employee_reads.py tests/test_read_only_gating.py tests/test_stdio_server.py -q`
Expected: failures with `Unknown tool: hibob_list_employee_fields` and the count assertions.

- [ ] **Step 3: Implement**

In `src/hibob_advanced_mcp/employees.py`, add imports:

```python
from .cache import NamedListCache
from .people_api import custom_tables, people_fields
from .people_fields import describe_field
```

Replace the start of `register_employee_tools` (signature through the early `return`) with the following, leaving the `hibob_terminate_employee` tool definition after it unchanged:

```python
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

    if read_only:
        return
```

In `tests/conftest.py`, pass the cache through:

```python
        register_employee_tools(
            mcp, read_only=read_only, client_factory=lambda: client, list_cache=list_cache
        )
```

In `README.md`:
- Read table: add the row
  `| \`hibob_list_employee_fields\` | \`GET /company/people/fields\` and \`GET /people/custom-tables/metadata\`, cached | 50/min |`
- Configuration table: "registers only the eighteen read tools" → "registers only the nineteen read tools".
- HiBob setup, step 2: add the paragraph
  `Reading and changing employee data (\`hibob_get_employee\`, \`hibob_update_employee\`) needs **People's data → People's fields**: **View** on the categories read, **Edit** on those changed, and **View history** to read a table's earlier rows. HiBob skips fields the service user may not edit without saying so; the update tool reads every change back to catch that.`

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add -A src tests README.md
git commit -m "Add hibob_list_employee_fields"
```

---

### Task 6: `hibob_get_employee`

**Files:**
- Modify: `src/hibob_advanced_mcp/employees.py`
- Modify: `tests/test_read_only_gating.py`, `tests/test_stdio_server.py`, `README.md`
- Test: `tests/test_tools_employee_reads.py`

**Interfaces:**
- Consumes: `employee_directory.find_employee`, `describe_candidates`; `people_api.people_fields`, `read_employee`, `read_table`, `custom_tables`, `HISTORY_TABLES`; `people_fields.PeopleField`, `find_fields`, `nearest_fields`, `read_field`.
- Produces: `employees.DEFAULT_EMPLOYEE_FIELDS: tuple[str, ...]`; `employees.require_employee(match: EmployeeMatch, ref: Any) -> dict[str, Any]` (raises `ValueError`), reused by Task 8.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools_employee_reads.py`:

```python
from people_data import EMPLOYEE_ID


async def test_get_employee_default_fields_with_display_labels(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await call_tool(mcp_server, "hibob_get_employee", {"employee": "jane@x.com"})
    )
    assert result["employee"]["id"] == EMPLOYEE_ID
    by_id = {entry["id"]: entry for entry in result["fields"]}
    assert by_id["work.title"]["value"] == "101"
    assert by_id["work.title"]["display"] == "Analyst"
    assert by_id["work.title"]["field"] == "Work > Job title"
    assert by_id["work.startDate"]["value"] == "2024-03-01"


async def test_get_employee_named_fields_by_label(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_get_employee",
            {"employee": "Jane Smith", "fields": ["Mobile phone", "Shirt size"]},
        )
    )
    assert [(e["id"], e["value"], e["display"]) for e in result["fields"]] == [
        ("home.mobilePhone", "07700 900000", None),
        ("work.custom.field_100", "L", "Large"),
    ]


async def test_get_employee_unknown_or_ambiguous_field_is_an_error(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    text = await call_tool(
        mcp_server,
        "hibob_get_employee",
        {"employee": EMPLOYEE_ID, "fields": ["Start date", "Job titel"]},
    )
    assert text.startswith("Error:")
    assert "Home > Start date" in text
    assert "Job titel" in text


async def test_get_employee_ambiguous_name_lists_candidates(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    text = await call_tool(mcp_server, "hibob_get_employee", {"employee": "Alex Lee"})
    assert text.startswith("Error:")
    assert "alex.lee2@x.com" in text


async def test_get_employee_history_reads_tables_and_reports_per_table_errors(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    mock_api.get(f"/people/{EMPLOYEE_ID}/work").mock(
        return_value=httpx.Response(
            200, json={"values": [{"id": 1, "effectiveDate": "2024-03-01"}]}
        )
    )
    mock_api.get(f"/people/custom-tables/{EMPLOYEE_ID}/about__table_1").mock(
        return_value=httpx.Response(200, json={"values": [{"id": 5, "column_1": "x"}]})
    )
    mock_api.get(f"/people/{EMPLOYEE_ID}/salaries").mock(
        return_value=httpx.Response(403, json={})
    )
    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_get_employee",
            {
                "employee": EMPLOYEE_ID,
                "fields": ["Job title"],
                "history": ["Work", "Certifications", "salary", "Holidays"],
            },
        )
    )
    history = result["history"]
    assert history["Work"]["rows"][0]["id"] == 1
    assert history["Certifications"]["rows"][0]["column_1"] == "x"
    assert "People's fields" in history["salary"]["error"]
    assert "Holidays" in history["Holidays"]["error"]
```

Edit `tests/test_read_only_gating.py`: add `"hibob_get_employee",` to `READ_TOOLS`.
Edit `tests/test_stdio_server.py`: `== 30` → `== 31` (both places); read-only `== 19` → `== 20`.

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_tools_employee_reads.py tests/test_read_only_gating.py tests/test_stdio_server.py -q`
Expected: failures with `Unknown tool: hibob_get_employee` and the counts.

- [ ] **Step 3: Implement**

Add to `src/hibob_advanced_mcp/employees.py` imports:

```python
from .employee_directory import EmployeeMatch, describe_candidates, find_employee
from .people_api import HISTORY_TABLES, read_employee, read_table
from .people_fields import PeopleField, find_fields, nearest_fields, read_field
```

Module-level helpers (above `register_employee_tools`):

```python
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
        by_id = {field.id: field for field in known}
        return [by_id.get(fid) or _placeholder(fid) for fid in DEFAULT_EMPLOYEE_FIELDS]
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
```

Inside `register_employee_tools`, after `hibob_list_employee_fields` and before `if read_only: return`:

```python
@mcp.tool(
    name="hibob_get_employee",
    annotations=ToolAnnotations(
        title="Get a HiBob employee's data", **read_annotations
    ),
)
async def hibob_get_employee(
    employee: Annotated[
        str | int,
        Field(
            description="The employee, by HiBob employee ID, work email or display name."
        ),
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
        person = require_employee(await find_employee(api, cache, employee), employee)
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
```

In `README.md`: Read table row
`| \`hibob_get_employee\` | \`POST /people/{id}\`, plus \`GET /people/{id}/<table>\` or \`GET /people/custom-tables/{id}/{table}\` per history table; names use \`POST /people/search\` | 100/min reads, 50/min tables |`
and "nineteen read tools" → "twenty read tools".

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add -A src tests README.md
git commit -m "Add hibob_get_employee"
```

---

### Task 7: `employee_values.py` and `employee_rows.py` — values and bodies

**Files:**
- Create: `src/hibob_advanced_mcp/employee_values.py`
- Create: `src/hibob_advanced_mcp/employee_rows.py`
- Test: `tests/test_employee_values.py`

**Interfaces:**
- Consumes: `people_fields.PeopleField`, `ROOT_PREFIX`; `envelopes.iso_date`, `normalize_id`.
- Produces:
  - `employee_values.LIST_TYPES: frozenset[str]`
  - `class NeedsInput(Exception)` with `.question: dict[str, Any]`
  - `coerce_value(field: PeopleField, value: Any) -> Any`
  - `employee_rows.put_body(values: dict[str, Any]) -> dict[str, Any]` (keys are JSON paths)
  - `employee_rows.same_value(sent: Any, read: Any) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_employee_values.py`:

```python
"""Coercing given values by field type, and building and checking writes."""

from __future__ import annotations

import pytest

from hibob_advanced_mcp.employee_rows import put_body, same_value
from hibob_advanced_mcp.employee_values import NeedsInput, coerce_value
from hibob_advanced_mcp.people_fields import normalize_people_fields
from people_data import FIELDS

BY_ID = {field.id: field for field in normalize_people_fields(FIELDS)}


@pytest.mark.parametrize(
    ("field_id", "given", "expected"),
    [
        ("work.startDate", " 2026-11-01 ", "2026-11-01"),
        ("about.custom.field_800", "12", 12),
        ("about.custom.field_800", "12.5", 12.5),
        ("about.custom.field_800", 7, 7),
        ("about.custom.field_700", True, True),
        ("about.custom.field_700", "No", False),
        ("home.mobilePhone", " 07700 900123 ", "07700 900123"),
        (
            "financial.custom.field_500",
            {"value": "5000", "currency": "gbp"},
            {"value": 5000, "currency": "GBP"},
        ),
    ],
)
def test_coerce_value(field_id: str, given, expected) -> None:
    assert coerce_value(BY_ID[field_id], given) == expected


@pytest.mark.parametrize(
    ("field_id", "given"),
    [
        ("work.startDate", "01/11/2026"),
        ("work.startDate", "2026-02-30"),
        ("about.custom.field_800", "1,000"),
        ("about.custom.field_700", "maybe"),
        ("home.mobilePhone", ["a"]),
        ("home.mobilePhone", None),
        ("financial.custom.field_500", {"value": 1, "currency": "pounds"}),
    ],
)
def test_coerce_value_refuses(field_id: str, given) -> None:
    with pytest.raises(ValueError):
        coerce_value(BY_ID[field_id], given)


def test_a_bare_amount_asks_for_its_currency() -> None:
    with pytest.raises(NeedsInput) as excinfo:
        coerce_value(BY_ID["financial.custom.field_500"], 5000)
    assert "currency" in excinfo.value.question["question"]
    assert excinfo.value.question["argument"] == "changes"


def test_put_body_nests_by_path_with_root_fields_at_the_top() -> None:
    assert put_body(
        {
            "root.firstName": "Janet",
            "home.mobilePhone": "1",
            "work.custom.field_100": "M",
            "about.custom.field_300": ["1"],
        }
    ) == {
        "firstName": "Janet",
        "home": {"mobilePhone": "1"},
        "work": {"custom": {"field_100": "M"}},
        "about": {"custom": {"field_300": ["1"]}},
    }


@pytest.mark.parametrize(
    ("sent", "read", "same"),
    [
        ("M", "M", True),
        (12, 12.0, True),
        (12, "12", True),
        (
            "3332883884017713999",
            {"id": "3332883884017713999", "email": "s@x.com"},
            True,
        ),
        (["1", "2"], ["2", "1"], True),
        (
            {"value": 5000, "currency": "GBP"},
            {"value": 5000.0, "currency": "GBP"},
            True,
        ),
        ({"value": 5000, "currency": "GBP"}, {"value": 5000, "currency": "EUR"}, False),
        (True, True, True),
        (True, "true", True),
        ("M", None, False),
        ("M", "L", False),
    ],
)
def test_same_value(sent, read, same: bool) -> None:
    assert same_value(sent, read) is same
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_employee_values.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'hibob_advanced_mcp.employee_rows'`.

- [ ] **Step 3: Implement**

Create `src/hibob_advanced_mcp/employee_values.py`:

```python
"""The values a user gives, as HiBob's employee writes take them.

List values and people are resolved against HiBob elsewhere; these pure
functions handle the rest by the field's metadata type, converting where the
meaning is unambiguous and refusing otherwise. A bare amount for a currency
field cannot be settled without the user, so it raises NeedsInput carrying
the question to put to them.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .envelopes import iso_date
from .people_fields import PeopleField

LIST_TYPES = frozenset({"list", "multi-list", "hierarchy-list"})
TEXT_TYPES = frozenset({"text", "text-area", "time"})
_NUMBER = re.compile(r"-?[0-9]+(\.[0-9]+)?")
_SHORT_DATE = re.compile(r"([0-9]{2})-([0-9]{2})")
_CURRENCY = re.compile(r"[A-Za-z]{3}")
_TRUE = frozenset({"true", "yes"})
_FALSE = frozenset({"false", "no"})


class NeedsInput(Exception):
    """A value only the user can settle; carries the question to ask them."""

    def __init__(self, question: dict[str, Any]) -> None:
        super().__init__(str(question.get("question", "")))
        self.question = question


def _number(field: PeopleField, value: Any) -> int | float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and _NUMBER.fullmatch(text := value.strip()):
        return float(text) if "." in text else int(text)
    raise ValueError(f"{field.qualified_label} must be a plain number, not {value!r}.")


def _currency(field: PeopleField, value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and set(value) == {"value", "currency"}:
        code = value["currency"]
        if isinstance(code, str) and _CURRENCY.fullmatch(code.strip()):
            return {
                "value": _number(field, value["value"]),
                "currency": code.strip().upper(),
            }
        raise ValueError(
            f"{field.qualified_label}'s currency must be a three-letter code "
            f"such as GBP, not {code!r}."
        )
    if isinstance(value, (int, float, str)) and not isinstance(value, bool):
        amount = _number(field, value)
        raise NeedsInput(
            {
                "argument": "changes",
                "question": (
                    f"In what currency is {field.label} {amount}? Give it as "
                    f'{{"value": {amount}, "currency": "GBP"}}.'
                ),
            }
        )
    raise ValueError(
        f'{field.qualified_label} must be {{"value": amount, "currency": code}}, '
        f"not {value!r}."
    )


def coerce_value(field: PeopleField, value: Any) -> Any:
    """``value`` as HiBob takes it for ``field``; list and person fields excluded."""
    if value is None:
        raise ValueError(
            f"{field.qualified_label} cannot be set to null; leave it out to keep "
            "its value."
        )
    kind = field.type
    if kind == "date":
        return iso_date(field.qualified_label, value)
    if kind == "short-date":
        match = _SHORT_DATE.fullmatch(value.strip()) if isinstance(value, str) else None
        if match:
            try:
                date(2000, int(match[1]), int(match[2]))
            except ValueError:
                pass
            else:
                return match[0]
        raise ValueError(
            f"{field.qualified_label} must be a month and day written MM-DD, "
            f"not {value!r}."
        )
    if kind == "number":
        return _number(field, value)
    if kind == "currency":
        return _currency(field, value)
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
            return value.strip().lower() in _TRUE
        raise ValueError(
            f"{field.qualified_label} must be true or false, not {value!r}."
        )
    if kind in TEXT_TYPES:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError(f"{field.qualified_label} must be text, not {value!r}.")
        return str(value).strip()
    raise ValueError(
        f"{field.qualified_label} is a {kind or 'untyped'} field, which this tool "
        "cannot set."
    )
```

Create `src/hibob_advanced_mcp/employee_rows.py`:

```python
"""HiBob employee write bodies, and checking what a read-back holds."""

from __future__ import annotations

from typing import Any

from .envelopes import normalize_id
from .people_fields import ROOT_PREFIX


def put_body(values: dict[str, Any]) -> dict[str, Any]:
    """PUT /people/{id}'s body: each value nested by its JSON path.

    "home.mobilePhone" goes under "home"; root fields ("root.firstName") go
    at the top level, as HiBob's reference shows.
    """
    body: dict[str, Any] = {}
    for path, value in values.items():
        if path.startswith(ROOT_PREFIX):
            path = path[len(ROOT_PREFIX) :]
        *parents, leaf = path.split(".")
        node = body
        for part in parents:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"{path} clashes with another change.")
            node = child
        node[leaf] = value
    return body


def same_value(sent: Any, read: Any) -> bool:
    """Whether a read-back holds what was sent, allowing for HiBob's shapes:
    a person read back as {"id", ...}, numbers as floats, lists reordered."""
    if isinstance(read, dict) and "id" in read and not isinstance(sent, dict):
        read = read["id"]
    if isinstance(sent, dict) and isinstance(read, dict):
        return normalize_id(sent.get("value")) == normalize_id(read.get("value")) and (
            str(sent.get("currency", "")).upper()
            == str(read.get("currency", "")).upper()
        )
    if isinstance(sent, list) and isinstance(read, list):
        return sorted(normalize_id(v) for v in sent) == sorted(
            normalize_id(v) for v in read
        )
    if isinstance(sent, bool):
        return str(sent).lower() == str(read).strip().lower()
    if sent is None or read is None:
        return sent is read
    return normalize_id(sent) == normalize_id(read)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_employee_values.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add src/hibob_advanced_mcp/employee_values.py src/hibob_advanced_mcp/employee_rows.py tests/test_employee_values.py
git commit -m "Add employee value coercion and write bodies"
```

---

### Task 8: `hibob_update_employee` — resolve, ask, refuse, write plain fields

**Files:**
- Create: `src/hibob_advanced_mcp/employee_updates.py`
- Modify: `src/hibob_advanced_mcp/employees.py` (register the update tool), `tests/conftest.py`
- Modify: `tests/test_read_only_gating.py`, `tests/test_stdio_server.py`
- Test: `tests/test_tools_employee_updates.py`

**Interfaces:**
- Consumes: everything from Tasks 3, 4, 6 (`require_employee` is not used here: an unmatched employee becomes a question), 7; `list_values.resolve_list_values`; `references.NOTHING_WRITTEN`.
- Produces:
  - `employee_updates.register_update_tools(mcp, *, client_factory, cache: NamedListCache, sleep: SleepFn) -> None`
  - `employee_updates.SleepFn = Callable[[float], Awaitable[None]]`
  - `register_employee_tools(..., sleep: SleepFn | None = None)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tools_employee_updates.py`:

```python
"""hibob_update_employee: plain fields, questions and refusals."""

from __future__ import annotations

import json
from datetime import date

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from conftest import call_tool
from people_data import EMPLOYEE_ID, MANAGER_ID, FakePeople


async def _update(mcp_server, **arguments) -> str:
    return await call_tool(mcp_server, "hibob_update_employee", arguments)


async def test_plain_fields_go_in_one_nested_put(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    text = await _update(
        mcp_server,
        employee="jane@x.com",
        changes={
            "Mobile phone": "07700 900123",
            "First name": "Janet",
            "Shirt size": "medium",
            "Languages": ["Spanish", "French"],
            "Buddy": "Sam Jones",
        },
    )
    assert fake.put.call_count == 1
    assert json.loads(fake.put.calls.last.request.content) == {
        "firstName": "Janet",
        "home": {"mobilePhone": "07700 900123"},
        "work": {"custom": {"field_100": "M", "field_400": MANAGER_ID}},
        "about": {"custom": {"field_300": ["1", "2"]}},
    }
    result = json.loads(text)
    assert result["status"] == "updated"
    shirt = next(a for a in result["applied"] if a["id"] == "work.custom.field_100")
    assert (shirt["to"], shirt["sent"], shirt["via"]) == ("medium", "M", "field")


async def test_employee_given_as_a_number(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    text = await _update(
        mcp_server, employee=int(EMPLOYEE_ID), changes={"Mobile phone": "1"}
    )
    assert json.loads(text)["status"] == "updated"
    assert fake.put.call_count == 1


async def test_questions_are_collected_and_nothing_is_written(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={
            "Start date": "2026-11-01",
            "Shirt sise": "Large",
            "Shirt size": "Large",
            "Buddy": "Alex Lee",
            "Bonus target": 5000,
            "Languages": "Spanish, French",
        },
    )
    result = json.loads(text)
    assert result["status"] == "needs_input"
    by_key = {q["key"]: q for q in result["questions"]}
    assert {c["label"] for c in by_key["Start date"]["candidates"]} == {
        "Work > Start date",
        "Home > Start date",
    }
    assert "Work > Shirt size" in [
        c["label"] for c in by_key["Shirt sise"]["candidates"]
    ]
    assert {c["id"] for c in by_key["Shirt size"]["candidates"]} == {"L", "L2"}
    assert len(by_key["Buddy"]["candidates"]) == 2
    assert "currency" in by_key["Bonus target"]["question"]
    assert by_key["Languages"]["candidates"]
    assert fake.writes == []


async def test_an_ambiguous_employee_is_a_question(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(mcp_server, employee="Alex Lee", changes={"Mobile phone": "1"})
    )
    assert result["status"] == "needs_input"
    assert result["questions"][0]["argument"] == "employee"
    assert fake.writes == []


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"Job title": "Head of Data"}, "not supported yet"),
        ({"City": "Leeds"}, "cannot be changed"),
        ({"Status": "Inactive"}, "cannot be changed"),
        ({"Mobile phone": None}, "null"),
        ({"Work > Start date": "01/11/2026"}, "YYYY-MM-DD"),
        ({"Email": "not-an-email"}, "not an email address"),
    ],
)
async def test_refusals_write_nothing(mock_api, mcp_server, changes, expected) -> None:
    fake = FakePeople(mock_api)
    text = await _update(mcp_server, employee=EMPLOYEE_ID, changes=changes)
    assert text.startswith("Error:")
    assert expected in text
    assert "Nothing was written" in text
    assert fake.writes == []


async def test_unknown_employee_is_an_error(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    text = await _update(
        mcp_server, employee="nobody@x.com", changes={"Mobile phone": "1"}
    )
    assert text.startswith("Error:")
    assert "No employee found" in text


async def test_a_future_effective_date_cannot_schedule_plain_fields(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Mobile phone": "1"},
        effective_date="2099-01-01",
    )
    assert text.startswith("Error:")
    assert "Home > Mobile phone" in text
    assert "immediately" in text
    assert fake.writes == []


async def test_todays_effective_date_is_fine_for_plain_fields(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Mobile phone": "1"},
        effective_date=date.today().isoformat(),
    )
    assert json.loads(text)["status"] == "updated"
    assert fake.put.call_count == 1


async def test_empty_changes_are_refused(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    text = await _update(mcp_server, employee=EMPLOYEE_ID, changes={})
    assert text.startswith("Error:")


async def test_update_is_absent_in_read_only_mode(server_factory) -> None:
    with pytest.raises(ToolError, match="Unknown tool"):
        await call_tool(
            server_factory(read_only=True),
            "hibob_update_employee",
            {"employee": EMPLOYEE_ID, "changes": {"Mobile phone": "1"}},
        )
```

Edit `tests/test_read_only_gating.py`: add `"hibob_update_employee",` to `WRITE_TOOLS` (not to `DESTRUCTIVE_TOOLS`).
Edit `tests/test_stdio_server.py`: `== 31` → `== 32` (both places); read-only stays `== 20`; add `"hibob_update"` is already covered by the `startswith(("hibob_create", "hibob_update", ...))` check.

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_tools_employee_updates.py -q`
Expected: failures with `Unknown tool: hibob_update_employee`.

- [ ] **Step 3: Implement `employee_updates.py`**

```python
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
from .employee_directory import find_employee
from .employee_rows import put_body
from .employee_values import LIST_TYPES, NeedsInput, coerce_value
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
                f"{target.qualified_label} names no list, so its value cannot be checked."
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
    if target.type == "employee-reference":
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
            Field(
                description="The employee, by HiBob employee ID, work email or display name."
            ),
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
```

In `src/hibob_advanced_mcp/employees.py`: add `import asyncio`, `from .employee_updates import SleepFn, register_update_tools`; add `sleep: SleepFn | None = None` as the last keyword parameter of `register_employee_tools`; at the end of the function (after `hibob_terminate_employee`):

```python
    register_update_tools(
        mcp, client_factory=client_factory, cache=cache, sleep=sleep or asyncio.sleep
    )
```

In `tests/conftest.py`, make the server's waits recordable (the `recorded_sleeps` fixture already exists):

```python
@pytest.fixture
def server_factory(client: HiBobClient, recorded_sleeps: list[float]):
    """Build a server whose tools talk to the test client."""

    async def fake_sleep(delay: float) -> None:
        recorded_sleeps.append(delay)

    def build(
        read_only: bool = False, list_cache: NamedListCache | None = None
    ) -> FastMCP:
        ...
        register_employee_tools(
            mcp,
            read_only=read_only,
            client_factory=lambda: client,
            list_cache=list_cache,
            sleep=fake_sleep,
        )
        ...
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add -A src tests
git commit -m "Add hibob_update_employee for plain fields"
```

---

### Task 9: Start date, email, write order, partial failure, 304 and read-back

**Files:**
- Modify: `src/hibob_advanced_mcp/employee_updates.py`
- Modify: `tests/people_data.py` (start-date and email routes on `FakePeople`)
- Modify: `README.md`
- Test: `tests/test_tools_employee_updates.py`

**Interfaces:**
- Consumes: `employee_rows.same_value`; `people_api.read_employee`; `people_fields.read_field`.
- Produces: final `hibob_update_employee` result shape: `status` ∈ {`updated`, `unchanged`, `partial`, `needs_input`}; keys `applied` (entries gain `from`), `warnings`, `unconfirmed`, `unconfirmed_note`, `verification_error`, `failed`, `not_sent`.

- [ ] **Step 1: Extend the fake**

In `FakePeople.__init__` (tests/people_data.py), add:

```python
self.start_date_status = 200
self.email_status = 200
self.start_date = mock_api.post(f"/employees/{EMPLOYEE_ID}/start-date").mock(
    side_effect=self._start_date
)
self.email = mock_api.put(f"/people/{EMPLOYEE_ID}/email").mock(side_effect=self._email)
```

and methods:

```python
def _start_date(self, request: httpx.Request) -> httpx.Response:
    self.writes.append("start date")
    if self.start_date_status != 200:
        return httpx.Response(self.start_date_status, json={"error": "Bad start date"})
    self.records[EMPLOYEE_ID]["work"]["startDate"] = jsonlib.loads(request.content)[
        "startDate"
    ]
    return httpx.Response(200)


def _email(self, request: httpx.Request) -> httpx.Response:
    self.writes.append("email")
    email = jsonlib.loads(request.content)["email"]
    if email == self.records[EMPLOYEE_ID]["email"]:
        return httpx.Response(304)
    if self.email_status != 200:
        return httpx.Response(self.email_status, json={})
    self.records[EMPLOYEE_ID]["email"] = email
    return httpx.Response(200)
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_tools_employee_updates.py`:

```python
async def test_writes_go_fields_then_start_date_then_email(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={
                "Email": "Janet@X.com",
                "Work > Start date": "2024-04-01",
                "Mobile phone": "1",
            },
            reason="Corrected",
        )
    )
    assert fake.writes == ["fields", "start date", "email"]
    assert json.loads(fake.start_date.calls.last.request.content) == {
        "startDate": "2024-04-01",
        "reason": "Corrected",
    }
    assert json.loads(fake.email.calls.last.request.content) == {"email": "janet@x.com"}
    assert result["status"] == "updated"
    vias = {a["id"]: a["via"] for a in result["applied"]}
    assert vias == {
        "home.mobilePhone": "field",
        "work.startDate": "start date endpoint",
        "root.email": "email endpoint",
    }
    assert any("verification" in w for w in result["warnings"])
    assert "unconfirmed" not in result


async def test_applied_changes_say_what_they_replaced(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await _update(
            mcp_server, employee=EMPLOYEE_ID, changes={"Shirt size": "Medium"}
        )
    )
    assert result["applied"][0]["from"] == "Large"


async def test_a_later_failure_is_partial_and_stops_the_rest(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    fake.start_date_status = 400
    result = json.loads(
        await _update(
            mcp_server,
            employee=EMPLOYEE_ID,
            changes={
                "Mobile phone": "1",
                "Work > Start date": "2024-04-01",
                "Email": "new@x.com",
            },
        )
    )
    assert fake.writes == ["fields", "start date"]
    assert result["status"] == "partial"
    assert [a["id"] for a in result["applied"]] == ["home.mobilePhone"]
    assert result["failed"]["write"] == "start date"
    assert "Bad start date" in result["failed"]["error"]
    assert result["not_sent"] == ["Basic info > Email"]


async def test_a_first_failure_is_an_error(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    fake.put_status = 400
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Mobile phone": "1", "Email": "new@x.com"},
    )
    assert text.startswith("Error:")
    assert fake.writes == ["fields"]


async def test_a_denied_write_names_the_categories_to_grant(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    fake.put_status = 403
    text = await _update(
        mcp_server,
        employee=EMPLOYEE_ID,
        changes={"Mobile phone": "1", "Shirt size": "Medium"},
    )
    assert text.startswith("Error:")
    assert "Edit on Home, Work" in text


async def test_304_means_nothing_changed(mock_api, mcp_server) -> None:
    fake = FakePeople(mock_api)
    fake.put_status = 304
    result = json.loads(
        await _update(mcp_server, employee=EMPLOYEE_ID, changes={"Mobile phone": "1"})
    )
    assert result["status"] == "unchanged"
    assert result["applied"] == []
    assert "changed nothing" in result["warnings"][0]


async def test_same_email_in_other_case_is_unchanged(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await _update(mcp_server, employee=EMPLOYEE_ID, changes={"Email": "JANE@x.com"})
    )
    assert result["status"] == "unchanged"


async def test_a_change_hibob_ignores_is_unconfirmed_after_rereading(
    mock_api, mcp_server, recorded_sleeps
) -> None:
    fake = FakePeople(mock_api)
    fake.apply_writes = False
    result = json.loads(
        await _update(mcp_server, employee=EMPLOYEE_ID, changes={"Mobile phone": "1"})
    )
    assert result["status"] == "updated"
    assert result["unconfirmed"] == [
        {"field": "Home > Mobile phone", "sent": "1", "read": "07700 900000"}
    ]
    assert "Edit on Home" in result["unconfirmed_note"]
    assert recorded_sleeps == [5.0, 10.0]


async def test_a_change_read_back_at_once_needs_no_wait(
    mock_api, mcp_server, recorded_sleeps
) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await _update(mcp_server, employee=EMPLOYEE_ID, changes={"Buddy": "Sam Jones"})
    )
    assert "unconfirmed" not in result
    assert recorded_sleeps == []
```

- [ ] **Step 3: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_tools_employee_updates.py -q`
Expected: the new tests fail (start date and email are still planned as `field` writes in one PUT; no `from`, `warnings`, `unconfirmed`, `partial`).

- [ ] **Step 4: Implement**

In `employee_updates.py`, add imports and constants:

```python
from .errors import HiBobApiError, format_exception
from .employee_rows import put_body, same_value
from .people_api import named_list, people_fields, read_employee
from .people_fields import (
    PeopleField,
    Route,
    find_fields,
    nearest_fields,
    read_field,
    route_for,
)

EMAIL_PATH = "/people/{employee_id}/email"
START_DATE_PATH = "/employees/{employee_id}/start-date"
# Email goes last: HiBob sends the employee a verification email.
WRITE_ORDER = ("field", "start_date", "email")
WRITE_NAMES = {"field": "fields", "start_date": "start date", "email": "work email"}
VIA = {"field": "field", "start_date": "start date endpoint", "email": "email endpoint"}
# HiBob documents reads lagging writes by up to 20 seconds.
READ_BACK_DELAYS = (0.0, 5.0, 10.0)
```

Replace `_applied` and `_apply` with:

```python
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
```

Update the tool docstring's Returns paragraph to:

```
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
        after about 15 seconds is listed under "unconfirmed".
```

In `README.md`:
- Write table row: `| \`hibob_update_employee\` | \`PUT /people/{id}\`, \`POST /employees/{id}/start-date\`, \`PUT /people/{id}/email\`, each sent once and read back | 10/min (email, start date 20/min) |`
- Configuration: "the eleven write tools" → "the twelve write tools".
- Add a paragraph after the terminate paragraph:
  `\`hibob_update_employee\` takes changes as field labels or IDs mapped to values given by name, and works out where each goes: plain fields in one \`PUT /people/{id}\`, the work email and start date through their own endpoints. List values and people (by name, email or ID) are matched exactly, ignoring case; anything unmatched or ambiguous, and an amount without a currency, comes back as \`{"status": "needs_input", "questions": [...]}\` with candidates and nothing written. Fields HiBob keeps a dated history of (job title, department, site, manager, employment, salary) are refused for now; adding dated rows is the next phase. An \`effective_date\` other than today is refused for plain fields, which HiBob changes immediately. Writes go plain fields, start date, then email, each once; a failure stops the rest (\`partial\`). HiBob skips fields the service user may not edit without saying so and its reads lag by up to 20 seconds, so every change is read back for up to about 15 seconds and anything still not visible is listed under \`unconfirmed\`.`

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
uvx --no-cache --from . hibob-advanced-mcp --test
git add -A src tests README.md
git commit -m "Route start date and email, read every change back"
```

---

### Task 10: Sandbox write checks for plain fields (each write needs the user's OK)

**Files:**
- Create: `scripts/check_people_write.py`
- Modify: `tests/fixtures/people/observations.md`

**Interfaces:**
- Consumes: the working token from Task 1.

- [ ] **Step 1: Ask the user**

Ask, and wait for an explicit yes: "To check plain-field writes I'd make **two** writes in the sandbox: set `<field>` on `<test employee>` to `<value>`, then set it back to its current value. I'd read it back every 2 seconds for up to 30 seconds to measure HiBob's lag. Which employee and which plain text field should I use, and may I make those two writes?" Do not continue without the answer.

- [ ] **Step 2: Write the gated script**

```python
"""Two sandbox writes, approved by the user: set a plain field, restore it.

Usage: uv run --no-project --with httpx --with python-dotenv \
  python scripts/check_people_write.py <employee_id> <json_path> <value>
"""

from __future__ import annotations

import json
import sys
import time

import httpx
from dotenv import dotenv_values

ALLOWED_WRITES = 2
sent = 0


def guard(request: httpx.Request) -> None:
    global sent
    if request.method == "GET" or (
        request.method == "POST" and request.url.path.count("/") == 3
    ):
        return
    if request.method == "PUT" and sent < ALLOWED_WRITES:
        sent += 1
        return
    raise RuntimeError(f"blocked {request.method} {request.url.path}")


def nest(path: str, value: object) -> dict:
    parts = path.removeprefix("root.").split(".")
    body: dict = {}
    node = body
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    return body


def read(client: httpx.Client, employee_id: str, path: str) -> object:
    response = client.post(f"/people/{employee_id}", json={"fields": [path]})
    response.raise_for_status()
    return response.json()


def main() -> None:
    employee_id, path, value = sys.argv[1], sys.argv[2], sys.argv[3]
    env = dotenv_values(".env")
    host = (env.get("HIBOB_API_HOST") or "").replace("https://", "").split("/")[0]
    if "sandbox" not in host:
        raise SystemExit("Refusing: HIBOB_API_HOST is not the sandbox.")
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
    before = read(client, employee_id, path)
    print("before:", json.dumps(before))
    original = input("Original value to restore (exactly as shown above): ")
    put = client.put(f"/people/{employee_id}", json=nest(path, value))
    print("PUT:", put.status_code, put.text[:300])
    start = time.monotonic()
    while time.monotonic() - start < 30:
        print(
            f"{time.monotonic() - start:5.1f}s:",
            json.dumps(read(client, employee_id, path)),
        )
        time.sleep(2)
    restore = client.put(f"/people/{employee_id}", json=nest(path, original))
    print("restore PUT:", restore.status_code, restore.text[:300])


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Dry-run the guard with writes blocked**

Run:

```bash
uv run --no-project --with httpx --with python-dotenv python - <<'EOF'
import importlib.util, httpx
spec = importlib.util.spec_from_file_location("c", "scripts/check_people_write.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
put = httpx.Request("PUT", "https://x/v1/people/1")
m.guard(put); m.guard(put)
try:
    m.guard(put); print("THIRD WRITE NOT BLOCKED")
except RuntimeError:
    print("third write blocked ok")
try:
    m.guard(httpx.Request("POST", "https://x/v1/people/1/work")); print("TABLE POST NOT BLOCKED")
except RuntimeError:
    print("table post blocked ok")
EOF
```

Expected: `third write blocked ok` and `table post blocked ok`.

- [ ] **Step 4: Run it once, as approved**

Run: `uv run --no-project --with httpx --with python-dotenv python scripts/check_people_write.py <employee_id> <json_path> <value>`
Record in `tests/fixtures/people/observations.md`: the PUT status, the read shape, and how many seconds until the new value appeared. If the lag exceeded 15 seconds, tell the user and propose changing `READ_BACK_DELAYS` (Task 9) to match.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_people_write.py tests/fixtures/people/observations.md
git commit -m "Record HiBob plain-field write behaviour from the sandbox"
```
