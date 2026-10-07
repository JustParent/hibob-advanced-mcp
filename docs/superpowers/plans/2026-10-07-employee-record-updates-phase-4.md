# Employee Record Updates (Phase 4: records) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `hibob_add_employee_record`, which adds one row to the tables HiBob keeps several rows in at once (variable pay, entitlements, deductions, equity, training, bank accounts, dependents, right-to-work documents and a company's custom tables), asking for anything missing and never guessing.

**Architecture:** A pure module `employee_records.py` holds the record types and their columns, matching, bodies, masking and read-back comparison. `people_api.read_bulk_rows` and `read_records` read a type's existing rows. The tool lives in a new `employee_record_adds.py` and reuses `employee_updates.resolve_value` for list, person, amount and date values. `hibob_list_employee_fields` lists the record types and `hibob_get_employee` can show the ones that are only readable in bulk.

**Tech Stack:** Python ≥3.10, `mcp` FastMCP, `httpx`, `pydantic`, `pytest` + `pytest-asyncio` (auto mode) + `respx`, `ruff`, `mypy`.

**Spec:** `docs/superpowers/specs/2026-10-07-employee-record-updates-design.md` (sections "A record", "Tools → hibob_add_employee_record" and the record table under "Background").

## Global Constraints

- Python 3.10 and 3.12 both run in CI: no 3.11+ APIs.
- `ruff check .`, `ruff format --check .` and `mypy` must pass; run `ruff format .` before each commit.
- Work directly on `master` (the user said not to make a branch); commit after each task; do not push.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Tool results are JSON from `json.dumps(payload, indent=2, default=str)`, or a string beginning `Error:` from `errors.format_exception`.
- Writes are never retried; reads go through `client.get`.
- Matching is exact, ignoring case; ambiguity returns candidates; an effective date is never defaulted; `null` is refused.
- Every check and read runs before the write; a refusal or question writes nothing.
- Sensitive columns (account numbers, IBAN, routing numbers, document numbers) are sent to HiBob in full but masked (last four characters) in every result, question and `unconfirmed` entry.
- Live checks (Task 4) write only to `david@harriethq.com` on the demo tenant, only dated 2030 where the type is dated, and nothing is ever deleted. **No account, IBAN, routing or document number is ever written to HiBob by this plan**, even on a demo tenant and even though the user said they are fine with it (a standing rule of the executor): a bank account is checked live with non-sensitive columns only (bank name, nickname, account type); right-to-work records are not written live at all (they also set the employee's right-to-work expiry field). The numbered paths are covered by mock tests and the user runs them.
- Tool counts after this phase: 20 read, 13 write (33 in all).
- Test commands use the repo venv: `.venv/bin/pytest`, `.venv/bin/ruff`, `.venv/bin/mypy`.

## Review Focus

1. **A retry after a lost response**: calling again with the same values must not add a second identical record. Pinned in Task 3 (`test_an_identical_record_is_not_added_twice`).
2. **A bank account number in a result**: it must come back masked everywhere (result row, `unconfirmed`, questions), while HiBob is sent the full number. Pinned in Task 3.
3. **A list type whose item ID differs from its name** (entitlement type): HiBob wants the name; the ID would be refused. Pinned in Task 3.
4. **A date on a record type that has none** (equity, training): refused rather than silently dropped; and **no date on one that needs it** (variable pay): a question. Pinned in Task 3.
5. **A column HiBob drops on write**: listed under `unconfirmed`, not reported as a clean add. Pinned in Task 3.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/hibob_advanced_mcp/employee_records.py` (new, pure) | Record types, columns, matching, bodies, masking, comparison. |
| `src/hibob_advanced_mcp/list_values.py` (modify) | `list_item_names`: item ID → name. |
| `src/hibob_advanced_mcp/people_api.py` (modify) | `read_bulk_rows`. |
| `src/hibob_advanced_mcp/employee_updates.py` (modify) | `_resolve_value` becomes public `resolve_value`. |
| `src/hibob_advanced_mcp/employee_record_adds.py` (new) | `read_records`, `hibob_add_employee_record`. |
| `src/hibob_advanced_mcp/employees.py` (modify) | Record types in the field listing; bulk record history; register the new tool. |
| `tests/people_data.py` (modify) | Lists and record endpoints on the fake HiBob. |
| `tests/test_employee_records.py` (new), `tests/test_tools_employee_reads.py`, `tests/test_tools_employee_records.py` (new), `tests/test_read_only_gating.py`, `tests/test_stdio_server.py` (modify) | Tests. |
| `README.md`, spec, `tests/fixtures/people/observations.md` (modify) | Documentation and live findings. |

---

### Task 1: Record types, pure

**Files:**
- Create: `src/hibob_advanced_mcp/employee_records.py`
- Modify: `src/hibob_advanced_mcp/list_values.py`
- Test: `tests/test_employee_records.py` (new)

**Interfaces:**
- Consumes: `employee_rows.same_value`, `people_fields.PeopleField`.
- Produces (exact signatures):
  - `Column(id, label, kind, required=False, list=None, send="id", options=(), sensitive=False)`; kinds `text number date amount boolean list multi-list employee unsupported`; `send` ∈ `id name int`
  - `RecordType(key, label, aliases, path, read, dated, columns, entry_id=False, custom=False)`; `read` ∈ `table bulk`; property `names -> set[str]`
  - `RECORD_TYPES: tuple[RecordType, ...]`
  - `custom_record_type(table: dict[str, Any]) -> RecordType` (a table as `people_fields.normalize_custom_tables` returns it)
  - `find_record_types(types, text) -> list[RecordType]`; `find_column(rt, text) -> Column | None`
  - `describe_record_type(rt) -> dict[str, Any]`
  - `as_field(rt, column) -> PeopleField`
  - `body_for(rt, values, day) -> dict[str, Any]`
  - `masked(rt, row) -> dict[str, Any]`; `mask_text(value) -> str`
  - `compare_record(sent, row) -> list[dict[str, Any]]`; `find_identical(rows, sent) -> dict | None`; `find_new_row(before, after, entry_id, sent) -> dict | None`
  - `list_values.list_item_names(items) -> dict[str, str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_employee_records.py`:

```python
"""Record types: matching, bodies, masking and comparing rows."""

from __future__ import annotations

import pytest

from hibob_advanced_mcp.employee_records import (
    RECORD_TYPES,
    as_field,
    body_for,
    compare_record,
    custom_record_type,
    describe_record_type,
    find_column,
    find_identical,
    find_new_row,
    find_record_types,
    mask_text,
    masked,
)
from hibob_advanced_mcp.list_values import list_item_names
from hibob_advanced_mcp.people_fields import normalize_custom_tables
from people_data import CUSTOM_TABLES

BY_KEY = {rt.key: rt for rt in RECORD_TYPES}
CERTS = custom_record_type(normalize_custom_tables(CUSTOM_TABLES)[0])
ALL = (*RECORD_TYPES, CERTS)


def test_every_record_type_is_well_formed() -> None:
    assert [rt.key for rt in RECORD_TYPES] == [
        "variable",
        "entitlement",
        "deduction",
        "equity",
        "training",
        "bank_account",
        "dependent",
        "right_to_work",
    ]
    for rt in RECORD_TYPES:
        ids = [c.id for c in rt.columns]
        assert len(ids) == len(set(ids)), rt.key
        assert rt.read in ("table", "bulk")
        for column in rt.columns:
            if column.kind in ("list", "multi-list"):
                assert column.list, (rt.key, column.id)
    assert {rt.key for rt in RECORD_TYPES if rt.dated} == {
        "variable",
        "entitlement",
        "deduction",
    }
    assert {rt.key for rt in RECORD_TYPES if rt.read == "bulk"} == {
        "entitlement",
        "deduction",
        "dependent",
        "right_to_work",
    }


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("Variable pay", "variable"),
        ("  variable   PAY ", "variable"),
        ("variable", "variable"),
        ("entitlement", "entitlement"),
        ("Bank accounts", "bank_account"),
        ("bank-accounts", "bank_account"),
        ("right to work", "right_to_work"),
        ("Dependents", "dependent"),
        ("Stock options", "equity"),
    ],
)
def test_record_types_are_found_by_label_key_alias_or_path(text: str, key: str) -> None:
    assert [rt.key for rt in find_record_types(ALL, text)] == [key]


def test_custom_tables_are_found_by_name_or_id_and_unknown_types_by_nothing() -> None:
    assert find_record_types(ALL, "certifications") == [CERTS]
    assert find_record_types(ALL, "about__table_1") == [CERTS]
    assert find_record_types(ALL, "holidays") == []
    with pytest.raises(ValueError):
        find_record_types(ALL, " ")


def test_columns_are_found_by_label_or_id_ignoring_case() -> None:
    variable = BY_KEY["variable"]
    column = find_column(variable, "payment PERIOD")
    assert column is not None and column.id == "paymentPeriod"
    assert find_column(variable, "amount") is find_column(variable, "Amount")
    assert find_column(variable, "nonsense") is None


def test_a_custom_table_keeps_its_columns_kinds_and_required_flags() -> None:
    assert (CERTS.path, CERTS.custom, CERTS.dated, CERTS.read) == (
        "about__table_1",
        True,
        False,
        "table",
    )
    certificate, expires = CERTS.columns
    assert (certificate.id, certificate.kind, certificate.required) == (
        "column_1",
        "list",
        True,
    )
    assert certificate.list == "certs"
    assert (expires.kind, expires.required) == ("date", False)


def test_body_for_adds_the_date_to_dated_types_and_wraps_custom_tables() -> None:
    values = {"amount": {"value": 1, "currency": "GBP"}}
    assert body_for(BY_KEY["variable"], values, "2030-01-01") == {
        **values,
        "effectiveDate": "2030-01-01",
    }
    assert body_for(BY_KEY["equity"], {"quantity": 5}, None) == {"quantity": 5}
    assert body_for(CERTS, {"column_1": "x"}, None) == {"values": [{"column_1": "x"}]}


def test_sensitive_columns_are_masked_to_their_last_four_characters() -> None:
    bank = BY_KEY["bank_account"]
    row = {
        "bankName": "Acme",
        "accountNumber": "12345678",
        "iban": "GB29NWBK60161331926819",
        "routingNumber": "123",
        "accountNickname": None,
    }
    assert masked(bank, row) == {
        "bankName": "Acme",
        "accountNumber": "****5678",
        "iban": "******************6819",
        "routingNumber": "***",
        "accountNickname": None,
    }
    assert mask_text("1234") == "****"


def test_compare_record_reports_what_differs() -> None:
    sent = {
        "amount": {"value": 5000, "currency": "GBP"},
        "variableType": "Bonus",
        "effectiveDate": "2030-01-01",
    }
    row = {
        "amount": {"value": 5000.0, "currency": "GBP"},
        "variableType": None,
        "effectiveDate": "2030-01-01",
    }
    assert compare_record(sent, row) == [
        {"column": "variableType", "sent": "Bonus", "read": None}
    ]


def test_find_identical_and_find_new_row() -> None:
    sent = {"firstName": "Ada", "surname": "Lovelace"}
    old = {"id": 1, "firstName": "Ada", "surname": "Byron"}
    same = {"id": 2, "firstName": "Ada", "surname": "Lovelace", "gender": None}
    assert find_identical([old], sent) is None
    assert find_identical([old, same], sent) is same
    assert find_new_row([old], [old, same], None, sent) is same
    assert find_new_row([old], [old, same], 2, sent) is same
    assert find_new_row([old], [old], None, sent) is None
    assert find_new_row([old], [old, same], 9, sent) is None
    other = {"id": 3, "firstName": "Bob", "surname": "X"}
    assert find_new_row([old], [old, other, same], None, sent) is same


def test_describe_record_type_lists_columns_with_required_flags() -> None:
    entry = describe_record_type(BY_KEY["entitlement"])
    assert entry["id"] == "entitlement"
    assert entry["dated"] is True
    by_id = {c["id"]: c for c in entry["columns"]}
    assert by_id["entitlement"]["required"] is True
    assert by_id["entitlement"]["list"] == "entitlementType"
    assert by_id["endDate"]["type"] == "date"
    grant = {c["id"]: c for c in describe_record_type(BY_KEY["equity"])["columns"]}
    assert grant["grantType"]["options"] == ["Initial Grant", "Merit Grant"]


def test_as_field_gives_the_value_resolver_the_types_it_knows() -> None:
    field = as_field(BY_KEY["variable"], find_column(BY_KEY["variable"], "amount"))
    assert (field.type, field.qualified_label) == ("currency", "Variable pay > Amount")
    listed = as_field(
        BY_KEY["variable"], find_column(BY_KEY["variable"], "Variable type")
    )
    assert (listed.type, listed.list_id) == ("list", "payType")


def test_list_item_names_maps_ids_to_names_through_a_tree() -> None:
    items = [
        {"id": "ET1", "name": "Lunch vouchers"},
        {
            "id": "G",
            "name": "Group",
            "children": [{"id": "ET2", "name": "Company Car"}],
        },
    ]
    assert list_item_names(items) == {"ET1": "Lunch vouchers", "ET2": "Company Car"}
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_employee_records.py -q 2>&1 | tail -4`
Expected: `ModuleNotFoundError: No module named 'hibob_advanced_mcp.employee_records'`.

- [ ] **Step 3: Implement `list_item_names`**

Append to `src/hibob_advanced_mcp/list_values.py`:

```python
def list_item_names(items: Any) -> dict[str, str]:
    """Each submittable item's ID mapped to its name, through any tree."""
    leaves: list[dict[str, Any]] = []
    branches: list[dict[str, Any]] = []
    _walk(items, "", leaves, branches)
    return {leaf["id"]: leaf["name"] for leaf in leaves if leaf["id"] is not None}
```

- [ ] **Step 4: Implement `employee_records.py`**

Create `src/hibob_advanced_mcp/employee_records.py`:

```python
"""Records HiBob keeps in tables that hold several rows at once.

Variable pay, entitlements, deductions, equity grants, training, bank accounts,
dependents, right-to-work documents and a company's custom tables. A new row
copies nothing: it is built from the columns the user gives. These pure
functions describe each type's columns, find the type and column a user means,
build the write body, hide sensitive values, and compare what HiBob holds
afterwards with what was sent.

The columns come from HiBob's API reference, checked where a demo tenant had
rows or lists (entitlement, deduction, the lists behind training, bank
accounts and variable pay); variable pay, dependents, right-to-work and bank
accounts had no rows to read, so those are as documented.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .employee_rows import same_value
from .people_fields import PeopleField


@dataclass(frozen=True)
class Column:
    id: str
    label: str
    kind: str
    required: bool = False
    list: str | None = None
    send: str = "id"
    options: tuple[str, ...] = ()
    sensitive: bool = False


@dataclass(frozen=True)
class RecordType:
    key: str
    label: str
    aliases: tuple[str, ...]
    path: str
    read: str
    dated: bool
    columns: tuple[Column, ...]
    entry_id: bool = False
    custom: bool = False

    @property
    def names(self) -> set[str]:
        return {_squash(n) for n in (self.label, self.key, self.path, *self.aliases)}


def _squash(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


RECORD_TYPES = (
    RecordType(
        "variable",
        "Variable pay",
        ("variable", "variable pay"),
        "variable",
        "table",
        True,
        (
            Column("variableType", "Variable type", "list", True, "payType"),
            Column("amount", "Amount", "amount", True),
            Column(
                "paymentPeriod", "Payment period", "list", True, "variablePayPeriod"
            ),
            Column("companyPercent", "Company percent", "number"),
            Column("departmentPercent", "Department percent", "number"),
            Column("individualPercent", "Individual percent", "number"),
        ),
        entry_id=True,
    ),
    RecordType(
        "entitlement",
        "Entitlement",
        ("entitlement", "entitlements"),
        "entitlement",
        "bulk",
        True,
        (
            Column(
                "entitlement",
                "Entitlement type",
                "list",
                True,
                "entitlementType",
                "name",
            ),
            Column("amount", "Amount", "amount", True),
            Column("endDate", "End date", "date"),
        ),
        entry_id=True,
    ),
    RecordType(
        "deduction",
        "Deduction",
        ("deduction", "deductions"),
        "deduction",
        "bulk",
        True,
        (
            Column(
                "deduction", "Deduction type", "list", True, "deductionType", "name"
            ),
            Column("amount", "Amount", "amount", True),
            Column("endDate", "End date", "date"),
        ),
        entry_id=True,
    ),
    RecordType(
        "equity",
        "Equity grant",
        ("equity", "equities", "equity grant", "stock options"),
        "equities",
        "table",
        False,
        (
            Column("quantity", "Quantity", "number", True),
            Column("equityType", "Equity type", "text", True),
            Column("grantDate", "Grant date", "date"),
            Column("vestingCommencementDate", "Vesting commencement date", "date"),
            Column("optionExpiration", "Option expiration", "date"),
            Column("exercisePrice", "Exercise price", "amount"),
            Column("grantAmount", "Grant amount", "amount"),
            Column("vestingTerm", "Vesting term", "text"),
            Column("taxPlan", "Tax plan", "text"),
            Column("specialTerms", "Special terms", "text"),
            Column("consentNumber", "Consent number", "text"),
            Column(
                "grantType",
                "Grant type",
                "text",
                options=("Initial Grant", "Merit Grant"),
            ),
            Column(
                "grantStatus",
                "Grant status",
                "text",
                options=("Granted", "Pending Approval"),
            ),
            Column("grantNumber", "Grant number", "text"),
            Column(
                "vestingSchedule",
                "Vesting schedule",
                "list",
                False,
                "vestingSchedule",
                "int",
            ),
        ),
    ),
    RecordType(
        "training",
        "Training",
        ("training", "trainings"),
        "training",
        "table",
        False,
        (
            Column("name", "Training name", "list", True, "trainingName"),
            Column("description", "Description", "text"),
            Column("cost", "Cost", "amount"),
            Column("status", "Status", "list", False, "trainingStatus"),
            Column("frequency", "Frequency", "list", False, "trainingFrequency"),
            Column("startDate", "Start date", "date"),
            Column("endDate", "End date", "date"),
        ),
    ),
    RecordType(
        "bank_account",
        "Bank account",
        ("bank account", "bank accounts", "bank-accounts"),
        "bank-accounts",
        "table",
        False,
        (
            Column("bankAccountType", "Account type", "list", False, "bankaccounttype"),
            Column("accountNickname", "Account nickname", "text"),
            Column("bankName", "Bank name", "text"),
            Column("branchAddress", "Branch address", "text"),
            Column("routingNumber", "Routing number", "text", sensitive=True),
            Column("accountNumber", "Account number", "text", sensitive=True),
            Column("bicOrSwift", "BIC or SWIFT", "text"),
            Column("iban", "IBAN", "text", sensitive=True),
            Column("allocation", "Allocation", "list", False, "allocation"),
            Column("amount", "Allocation amount", "number"),
            Column("useForBonus", "Use for bonus", "boolean"),
        ),
    ),
    RecordType(
        "dependent",
        "Dependent",
        ("dependent", "dependents"),
        "dependents",
        "bulk",
        False,
        (
            Column("firstName", "First name", "text"),
            Column("surname", "Surname", "text"),
            Column("birthDate", "Birth date", "date"),
            Column("gender", "Gender", "list", False, "legalGender"),
        ),
        entry_id=True,
    ),
    RecordType(
        "right_to_work",
        "Right to work",
        ("right to work", "right-to-work"),
        "right-to-work",
        "bulk",
        False,
        (
            Column("type", "Document type", "text"),
            Column("documentId", "Document ID", "text"),
            Column("number", "Document number", "text", sensitive=True),
            Column("issuedBy", "Issued by", "text"),
            Column("validFrom", "Valid from", "date"),
            Column("expirationDate", "Expiration date", "date"),
            Column("applicationDate", "Application date", "date"),
        ),
        entry_id=True,
    ),
)

_CUSTOM_KINDS = {
    "text": "text",
    "text-area": "text",
    "number": "number",
    "date": "date",
    "list": "list",
    "multi-list": "multi-list",
    "multi_list": "multi-list",
    "hierarchy-list": "list",
    "list_id": "list",
    "currency": "amount",
    "employee-reference": "employee",
    "employee": "employee",
    "boolean": "boolean",
}
_FIELD_TYPES = {
    "text": "text",
    "number": "number",
    "date": "date",
    "amount": "currency",
    "boolean": "boolean",
    "list": "list",
    "multi-list": "multi-list",
    "employee": "employee-reference",
    "unsupported": "document",
}


def custom_record_type(table: dict[str, Any]) -> RecordType:
    """A custom table, as people_fields.normalize_custom_tables returns it."""
    columns = tuple(
        Column(
            id=str(c["id"]),
            label=str(c["label"]),
            kind=_CUSTOM_KINDS.get(str(c.get("type")), "unsupported"),
            required=bool(c.get("required")),
            list=c.get("list"),
        )
        for c in table.get("columns", [])
    )
    return RecordType(
        key=str(table["id"]),
        label=str(table["name"]),
        aliases=(),
        path=str(table["id"]),
        read="table",
        dated=False,
        columns=columns,
        custom=True,
    )


def find_record_types(types: Any, text: Any) -> list[RecordType]:
    """The record types ``text`` names exactly, by label, key, path or alias."""
    wanted = _squash(text)
    if not wanted:
        raise ValueError("record_type must not be empty.")
    return [rt for rt in types if wanted in rt.names]


def find_column(rt: RecordType, text: Any) -> Column | None:
    wanted = _squash(text)
    return next(
        (c for c in rt.columns if wanted in (_squash(c.label), _squash(c.id))), None
    )


def describe_record_type(rt: RecordType) -> dict[str, Any]:
    """A record type as hibob_list_employee_fields shows it."""
    columns = []
    for column in rt.columns:
        entry: dict[str, Any] = {
            "id": column.id,
            "label": column.label,
            "type": column.kind,
            "required": column.required,
        }
        if column.list:
            entry["list"] = column.list
        if column.options:
            entry["options"] = list(column.options)
        columns.append(entry)
    return {"id": rt.key, "label": rt.label, "dated": rt.dated, "columns": columns}


def as_field(rt: RecordType, column: Column) -> PeopleField:
    """A column as the value resolver sees a field."""
    return PeopleField(
        id=column.id,
        label=column.label,
        category=rt.label,
        category_id=rt.key,
        type=_FIELD_TYPES[column.kind],
        list_id=column.list,
        json_path=column.id,
        historical=False,
        calculated=False,
    )


def body_for(rt: RecordType, values: dict[str, Any], day: str | None) -> dict[str, Any]:
    """The write body: the values, dated if the type is, wrapped for custom tables."""
    row = dict(values)
    if rt.dated and day:
        row["effectiveDate"] = day
    return {"values": [row]} if rt.custom else row


def mask_text(value: Any) -> str:
    text = str(value)
    if len(text) <= 4:
        return "*" * len(text)
    return "*" * (len(text) - 4) + text[-4:]


def masked(rt: RecordType, row: dict[str, Any]) -> dict[str, Any]:
    """``row`` with its sensitive columns hidden but for their last four characters."""
    hidden = {c.id for c in rt.columns if c.sensitive}
    return {
        key: mask_text(value) if key in hidden and value not in (None, "") else value
        for key, value in row.items()
    }


def compare_record(sent: dict[str, Any], row: dict[str, Any]) -> list[dict[str, Any]]:
    """The columns of ``row`` (as HiBob holds it) that differ from ``sent``."""
    return [
        {"column": column, "sent": value, "read": row.get(column)}
        for column, value in sent.items()
        if not same_value(value, row.get(column))
    ]


def find_identical(
    rows: list[dict[str, Any]], sent: dict[str, Any]
) -> dict[str, Any] | None:
    """An existing row holding every value in ``sent``."""
    return next((row for row in rows if not compare_record(sent, row)), None)


def find_new_row(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    entry_id: Any,
    sent: dict[str, Any],
) -> dict[str, Any] | None:
    """The row a write added: by the entry ID HiBob returned, else the one that
    was not there before (the closest to ``sent`` if several are)."""
    if entry_id is not None:
        return next((r for r in after if str(r.get("id")) == str(entry_id)), None)
    known = {str(r.get("id")) for r in before}
    fresh = [r for r in after if str(r.get("id")) not in known]
    if len(fresh) <= 1:
        return fresh[0] if fresh else None
    return min(fresh, key=lambda r: len(compare_record(sent, r)))
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest -q 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add src/hibob_advanced_mcp/employee_records.py src/hibob_advanced_mcp/list_values.py tests/test_employee_records.py
git commit -q -m "Add the record types and their pure logic

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Read records; list and show them

**Files:**
- Modify: `src/hibob_advanced_mcp/people_api.py`, `src/hibob_advanced_mcp/employees.py`
- Modify: `tests/people_data.py` (lists and record endpoints on the fake)
- Test: `tests/test_employee_directory.py`, `tests/test_tools_employee_reads.py`

**Interfaces:**
- Consumes: `employee_records` (Task 1).
- Produces: `people_api.read_bulk_rows(client, employee_id: str, table: str) -> list[dict[str, Any]]` (newest first; raises `ValueError` if HiBob reports an error for the employee); `hibob_list_employee_fields` result gains `record_types` (a list of `describe_record_type` entries, filtered by `search`); `hibob_get_employee`'s `history` accepts entitlement, deduction, dependents and right to work, and masks bank-account and right-to-work numbers.

- [ ] **Step 1: Give the fake HiBob records and lists**

In `tests/people_data.py`, add to `LISTS`:

```python
    "payType": {
        "name": "payType",
        "values": [
            {"id": "Bonus", "name": "Bonus", "value": "Bonus"},
            {"id": "Executive bonus", "name": "Executive bonus", "value": "Executive bonus"},
            {"id": "Commission", "name": "Commission", "value": "Commission"},
        ],
    },
    "variablePayPeriod": {
        "name": "variablePayPeriod",
        "values": [
            {"id": "Monthly", "name": "Monthly", "value": "Monthly"},
            {"id": "Annual", "name": "Annual", "value": "Annual"},
            {"id": "Quarterly", "name": "Quarterly", "value": "Quarterly"},
        ],
    },
    "entitlementType": {
        "name": "entitlementType",
        "values": [
            {"id": "ET1", "name": "Lunch vouchers", "value": "Lunch vouchers"},
            {"id": "ET2", "name": "Company Car", "value": "Company Car"},
        ],
    },
    "deductionType": {
        "name": "deductionType",
        "values": [
            {"id": "Company Car", "name": "Company Car", "value": "Company Car"},
            {"id": "Cycle to work", "name": "Cycle to work", "value": "Cycle to work"},
        ],
    },
    "trainingName": {
        "name": "trainingName",
        "values": [{"id": "Training", "name": "Training", "value": "Training"}],
    },
    "trainingStatus": {
        "name": "trainingStatus",
        "values": [
            {"id": "Invited", "name": "Invited", "value": "Invited"},
            {"id": "Completed", "name": "Completed", "value": "Completed"},
        ],
    },
    "trainingFrequency": {
        "name": "trainingFrequency",
        "values": [
            {"id": "Once", "name": "Once", "value": "Once"},
            {"id": "Yearly", "name": "Yearly", "value": "Yearly"},
        ],
    },
    "bankaccounttype": {
        "name": "bankaccounttype",
        "values": [
            {"id": "Current", "name": "Current", "value": "Current"},
            {"id": "Savings", "name": "Savings", "value": "Savings"},
        ],
    },
    "allocation": {
        "name": "allocation",
        "values": [
            {"id": "percent", "name": "%", "value": "%"},
            {"id": "remaining", "name": "Remaining", "value": "Remaining"},
        ],
    },
    "legalGender": {
        "name": "legalGender",
        "values": [
            {"id": "Female", "name": "Female", "value": "Female"},
            {"id": "Male", "name": "Male", "value": "Male"},
        ],
    },
    "certs": {
        "name": "certs",
        "values": [
            {"id": "C1", "name": "First aid", "value": "First aid"},
            {"id": "C2", "name": "Fire warden", "value": "Fire warden"},
        ],
    },
```

Above `class FakePeople` add:

```python
# Where each record type is read (a table read or a bulk read) and whether
# HiBob answers a write with {"entryId": n}.
RECORD_READS = {
    "variable": "table",
    "equities": "table",
    "training": "table",
    "bank-accounts": "table",
    "entitlement": "bulk",
    "deduction": "bulk",
    "dependents": "bulk",
    "right-to-work": "bulk",
    "about__table_1": "custom",
}
RECORD_ENTRY_IDS = {
    "variable",
    "entitlement",
    "deduction",
    "dependents",
    "right-to-work",
}
```

In `FakePeople.__init__`, after the `for path in self.tables:` loop, add:

```python
        self.records_by_path: dict[str, list[dict[str, Any]]] = {
            path: [] for path in RECORD_READS
        }
        for path, how in RECORD_READS.items():
            if how == "bulk":
                mock_api.get(f"/bulk/people/{path}").mock(
                    side_effect=self._record_bulk_read(path)
                )
                mock_api.post(f"/people/{EMPLOYEE_ID}/{path}").mock(
                    side_effect=self._record_write(path)
                )
            elif how == "custom":
                base = f"/people/custom-tables/{EMPLOYEE_ID}/{path}"
                mock_api.get(base).mock(side_effect=self._record_read(path))
                mock_api.post(base).mock(side_effect=self._record_write(path))
            else:
                mock_api.get(f"/people/{EMPLOYEE_ID}/{path}").mock(
                    side_effect=self._record_read(path)
                )
                mock_api.post(f"/people/{EMPLOYEE_ID}/{path}").mock(
                    side_effect=self._record_write(path)
                )
```

Add these methods to `FakePeople`:

```python
def add_record(self, path: str, **columns: Any) -> dict[str, Any]:
    rows = self.records_by_path[path]
    row = {"id": max((r["id"] for r in rows), default=100) + 1, **columns}
    rows.append(row)
    return row


def _record_read(self, path: str):
    def handler(request: httpx.Request) -> httpx.Response:
        self.reads.append(path)
        return httpx.Response(200, json={"values": self.records_by_path[path]})

    return handler


def _record_bulk_read(self, path: str):
    def handler(request: httpx.Request) -> httpx.Response:
        self.reads.append(path)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"employeeId": EMPLOYEE_ID, "values": self.records_by_path[path]}
                ],
                "response_metadata": {"next_cursor": None},
                "errors": self.bulk_errors.get(path, []),
            },
        )

    return handler


def _record_write(self, path: str):
    def handler(request: httpx.Request) -> httpx.Response:
        body = jsonlib.loads(request.content)
        row = dict(body["values"][0]) if "values" in body else dict(body)
        self.writes.append(f"record:{path}")
        self.posted.append((path, body))
        status = self.row_status.get(path, 200)
        if status != 200:
            error = (
                {"error": f"Duplicate effective date for {path}."}
                if status == 400
                else {}
            )
            return httpx.Response(status, json=error)
        rows = self.records_by_path[path]
        duplicate = path == "deduction" and any(
            r.get("effectiveDate") == row.get("effectiveDate")
            and r.get("deduction") == row.get("deduction")
            for r in rows
        )
        if duplicate:
            return httpx.Response(
                400, json={"error": "Duplicate effective date for deduction."}
            )
        for column in self.drop_on_write.get(path, ()):
            row[column] = None
        stored = self.add_record(path, **row)
        if path in RECORD_ENTRY_IDS:
            return httpx.Response(200, json={"entryId": stored["id"]})
        return httpx.Response(200)

    return handler
```

and in `__init__` (next to `self.reads`), add `self.bulk_errors: dict[str, list[dict[str, Any]]] = {}`.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_employee_directory.py`:

```python
async def test_read_bulk_rows_returns_one_employees_rows_newest_first(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    fake = FakePeople(mock_api)
    fake.add_record(
        "entitlement", effectiveDate="2025-01-01", entitlement="Company Car"
    )
    fake.add_record(
        "entitlement", effectiveDate="2026-01-01", entitlement="Lunch vouchers"
    )
    rows = await read_bulk_rows(client, EMPLOYEE_ID, "entitlement")
    assert [r["effectiveDate"] for r in rows] == ["2026-01-01", "2025-01-01"]


async def test_read_bulk_rows_raises_when_hibob_reports_an_error_for_the_employee(
    client: HiBobClient, mock_api: respx.MockRouter
) -> None:
    fake = FakePeople(mock_api)
    fake.bulk_errors["deduction"] = [
        {EMPLOYEE_ID: {"error": "MISSING_PERMISSION", "message": "No access"}}
    ]
    with pytest.raises(ValueError, match="No access"):
        await read_bulk_rows(client, EMPLOYEE_ID, "deduction")
```

(Add `import pytest` and `read_bulk_rows` to that file's imports: `from hibob_advanced_mcp.people_api import people_fields, read_bulk_rows, read_employee, read_table`.)

Append to `tests/test_tools_employee_reads.py`:

```python
async def test_list_fields_lists_the_record_types_and_custom_tables(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    result = json.loads(await call_tool(mcp_server, "hibob_list_employee_fields", {}))
    by_id = {entry["id"]: entry for entry in result["record_types"]}
    assert by_id["variable"]["dated"] is True
    assert {c["id"] for c in by_id["variable"]["columns"] if c["required"]} == {
        "variableType",
        "amount",
        "paymentPeriod",
    }
    assert by_id["about__table_1"]["label"] == "Certifications"
    assert by_id["about__table_1"]["columns"][0]["required"] is True


async def test_list_fields_search_narrows_record_types(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await call_tool(mcp_server, "hibob_list_employee_fields", {"search": "bonus"})
    )
    assert [entry["id"] for entry in result["record_types"]] == ["bank_account"]
    result = json.loads(
        await call_tool(mcp_server, "hibob_list_employee_fields", {"search": "certif"})
    )
    assert [entry["id"] for entry in result["record_types"]] == ["about__table_1"]


async def test_history_shows_bulk_only_records_and_masks_bank_numbers(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    fake.add_record(
        "entitlement", effectiveDate="2026-01-01", entitlement="Company Car"
    )
    fake.add_record("bank-accounts", bankName="Acme", accountNumber="12345678")
    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_get_employee",
            {
                "employee": EMPLOYEE_ID,
                "fields": ["Job title"],
                "history": ["entitlement", "bank accounts", "right to work"],
            },
        )
    )
    history = result["history"]
    assert history["entitlement"]["rows"][0]["entitlement"] == "Company Car"
    assert history["bank accounts"]["rows"][0]["accountNumber"] == "****5678"
    assert history["bank accounts"]["rows"][0]["bankName"] == "Acme"
    assert history["right to work"]["rows"] == []
```

- [ ] **Step 3: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_employee_directory.py tests/test_tools_employee_reads.py -q 2>&1 | tail -6`
Expected: failures: no `read_bulk_rows`, no `record_types`, history does not know the bulk record types and does not mask.

- [ ] **Step 4: Implement**

In `src/hibob_advanced_mcp/people_api.py` add (below `read_table`):

```python
BULK_PATH = "/bulk/people/{table}"


async def read_bulk_rows(
    client: HiBobClient, employee_id: str, table: str
) -> list[dict[str, Any]]:
    """One employee's rows of a table HiBob only reads in bulk, newest first.

    Raises ValueError if HiBob reports an error for the employee (its bulk
    reads answer 200 with the error in the body).
    """
    path = (
        BULK_PATH.format(table=quote(table, safe=""))
        + f"?employeeIds={quote(employee_id, safe='')}&limit=200"
    )
    payload = await client.get(path)
    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for entry in payload.get("errors") or []:
            if isinstance(entry, dict) and employee_id in entry:
                detail = entry[employee_id]
                message = detail.get("message") if isinstance(detail, dict) else detail
                raise ValueError(
                    f"HiBob would not return this employee's {table} rows: {message}"
                )
        for result in payload.get("results") or []:
            if (
                isinstance(result, dict)
                and str(result.get("employeeId")) == employee_id
            ):
                rows += [r for r in result.get("values") or [] if isinstance(r, dict)]
    rows.sort(key=lambda row: str(row.get("effectiveDate") or ""), reverse=True)
    return rows
```

In `src/hibob_advanced_mcp/employees.py`:

- Add imports: `from .employee_records import RECORD_TYPES, custom_record_type, describe_record_type, find_record_types, masked` and `read_bulk_rows` to the `people_api` import.
- Add this helper above `register_employee_tools`:

```python
async def _record_types(api: HiBobClient, cache: NamedListCache) -> list[Any]:
    """The built-in record types and the company's custom tables."""
    try:
        tables = await custom_tables(api, cache)
    except Exception:
        tables = []
    return [*RECORD_TYPES, *(custom_record_type(t) for t in tables)]
```

- In `hibob_list_employee_fields`, replace the block from `text = (search or "").strip().lower()` to the `result.update({...})` call with:

```python
text = (search or "").strip().lower()
types = await _record_types(api, cache)
if text:
    fields = [
        f
        for f in fields
        if text in f.label.lower() or text in f.id.lower() or text in f.category.lower()
    ]
    tables = [
        t
        for t in tables
        if text in t["name"].lower()
        or any(text in c["label"].lower() for c in t["columns"])
    ]
    types = [
        t
        for t in types
        if text in t.label.lower() or any(text in c.label.lower() for c in t.columns)
    ]
result.update(
    {
        "count": len(fields),
        "fields": [describe_field(f) for f in fields],
        "custom_tables": tables,
        "record_types": [describe_record_type(t) for t in types],
    }
)
return _dump(result)
```

(Remove the old `result.update(...)` and its `return`. Update the docstring: add "and record_types: the tables that hold several rows (variable pay, entitlements, equity, training, bank accounts, dependents, right to work, custom tables) with their columns, which are required, and whether they need a date" to the Returns text.)

- In `_history`, directly after the `if key in HISTORY_TABLES:` branch's `continue` and before `tables = await custom_tables(api, cache)`, add:

```python
            bulk = next(
                (rt for rt in RECORD_TYPES if rt.read == "bulk" and key in rt.names),
                None,
            )
            if bulk is not None:
                rows = await read_bulk_rows(api, employee_id, bulk.path)
                out[name] = {
                    "rows": [masked(bulk, row) for row in rows],
                    "restricted_columns": {},
                }
                continue
```

and change the `HISTORY_TABLES` branch's assignment to mask what it returns:

```python
            if key in HISTORY_TABLES:
                data = await read_table(api, employee_id, HISTORY_TABLES[key])
                record = next(iter(find_record_types(RECORD_TYPES, key)), None)
                if record is not None:
                    data["rows"] = [masked(record, row) for row in data["rows"]]
                out[name] = data
                continue
```

Check `find_record_types(RECORD_TYPES, key)` returns the bank-account type for "bank accounts" and nothing for "work" (an empty list; `next(iter([]), None)` is None).

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest -q 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
git add -A src tests
git commit -q -m "Read record tables, list the record types, mask bank numbers

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: hibob_add_employee_record

**Files:**
- Create: `src/hibob_advanced_mcp/employee_record_adds.py`
- Modify: `src/hibob_advanced_mcp/employee_updates.py` (`_resolve_value` → `resolve_value`), `src/hibob_advanced_mcp/employees.py` (register)
- Modify: `tests/test_read_only_gating.py`, `tests/test_stdio_server.py`, `README.md`
- Test: `tests/test_tools_employee_records.py` (new)

**Interfaces:**
- Consumes: Tasks 1 and 2; `employee_updates.resolve_value(api, cache, field, given, *, bare_amount_ok=False)`, `SleepFn`, `READ_BACK_DELAYS`; `employee_tables.to_wire`; `list_values.list_item_names`.
- Produces: `employee_record_adds.register_record_tools(mcp, *, client_factory, cache, sleep)`; the tool `hibob_add_employee_record(employee, record_type, values, effective_date=None)`.

- [ ] **Step 1: Write the failing tests**

Edit `tests/test_read_only_gating.py`: add `"hibob_add_employee_record",` to `WRITE_TOOLS` (not to `DESTRUCTIVE_TOOLS`). Edit `tests/test_stdio_server.py`: the two `== 32` become `== 33`.

Create `tests/test_tools_employee_records.py`:

```python
"""hibob_add_employee_record: one new row in a table that holds several."""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from conftest import call_tool
from people_data import EMPLOYEE_ID, FakePeople


async def _add(mcp_server, **arguments) -> str:
    return await call_tool(mcp_server, "hibob_add_employee_record", arguments)


VARIABLE = {
    "Variable type": "bonus",
    "Amount": {"value": 5000, "currency": "gbp"},
    "Payment period": "annual",
}


async def test_variable_pay_is_added_by_labels_and_read_back(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee="jane@x.com",
            record_type="Variable pay",
            values=VARIABLE,
            effective_date="2030-01-01",
        )
    )
    expected = {
        "variableType": "Bonus",
        "amount": {"value": 5000, "currency": "GBP"},
        "paymentPeriod": "Annual",
        "effectiveDate": "2030-01-01",
    }
    assert fake.posted == [("variable", expected)]
    assert result["status"] == "added"
    assert result["record_type"] == "Variable pay"
    assert result["employee"]["id"] == EMPLOYEE_ID
    assert result["entry_id"] == fake.records_by_path["variable"][0]["id"]
    assert result["row"] == expected
    assert result["verified"] is True


async def test_missing_required_columns_come_back_as_a_question(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="variable pay",
            values={"Amount": {"value": 1, "currency": "GBP"}},
            effective_date="2030-01-01",
        )
    )
    assert result["status"] == "needs_input"
    [question] = result["questions"]
    assert question["argument"] == "values"
    missing = {m["column"]: m for m in question["missing"]}
    assert set(missing) == {"variableType", "paymentPeriod"}
    assert "Bonus" in missing["variableType"]["options"]
    assert fake.writes == []


async def test_a_dated_type_asks_for_the_date_and_an_undated_one_refuses_it(
    mock_api, mcp_server
):
    fake = FakePeople(mock_api)
    asked = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="variable pay",
            values=VARIABLE,
        )
    )
    assert asked["status"] == "needs_input"
    assert asked["questions"][0]["argument"] == "effective_date"
    text = await _add(
        mcp_server,
        employee=EMPLOYEE_ID,
        record_type="training",
        values={"Training name": "Training"},
        effective_date="2030-01-01",
    )
    assert text.startswith("Error:")
    assert "no effective date" in text
    assert "Nothing was written" in text
    assert fake.writes == []


async def test_entitlement_is_sent_by_the_name_not_the_id(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="Entitlement",
            values={
                "Entitlement type": "ET1",
                "Amount": {"value": 150, "currency": "GBP"},
            },
            effective_date="2030-01-01",
        )
    )
    assert fake.posted[0][1]["entitlement"] == "Lunch vouchers"
    assert result["status"] == "added"
    assert result["verified"] is True


async def test_an_identical_record_is_not_added_twice(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    arguments = dict(
        employee=EMPLOYEE_ID,
        record_type="Deduction",
        values={
            "Deduction type": "Company Car",
            "Amount": {"value": 70, "currency": "GBP"},
        },
        effective_date="2030-01-01",
    )
    first = json.loads(await _add(mcp_server, **arguments))
    second = json.loads(await _add(mcp_server, **arguments))
    assert first["status"] == "added"
    assert second["status"] == "unchanged"
    assert "already has" in second["warnings"][0]
    assert second["entry_id"] == first["entry_id"]
    assert fake.writes == ["record:deduction"]


async def test_a_duplicate_hibob_refuses_says_records_are_only_added(
    mock_api, mcp_server
):
    fake = FakePeople(mock_api)
    fake.add_record(
        "deduction",
        effectiveDate="2030-01-01",
        deduction="Company Car",
        amount={"value": 1, "currency": "GBP"},
    )
    text = await _add(
        mcp_server,
        employee=EMPLOYEE_ID,
        record_type="Deduction",
        values={
            "Deduction type": "Company Car",
            "Amount": {"value": 70, "currency": "GBP"},
        },
        effective_date="2030-01-01",
    )
    assert text.startswith("Error:")
    assert "Duplicate effective date" in text
    assert "only adds rows" in text


async def test_equity_is_undated_with_a_fixed_vocabulary(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="equity",
            values={
                "Quantity": "100",
                "Equity type": "Options",
                "Grant type": "merit grant",
                "Grant date": "2026-01-15",
            },
        )
    )
    assert fake.posted == [
        (
            "equities",
            {
                "quantity": 100,
                "equityType": "Options",
                "grantType": "Merit Grant",
                "grantDate": "2026-01-15",
            },
        )
    ]
    assert result["status"] == "added"
    assert "entry_id" not in result
    assert result["verified"] is True
    bad = await _add(
        mcp_server,
        employee=EMPLOYEE_ID,
        record_type="equity",
        values={"Quantity": 1, "Equity type": "Options", "Grant type": "Gift"},
    )
    assert bad.startswith("Error:") and "Initial Grant" in bad


async def test_training_resolves_its_lists_and_amount(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    await _add(
        mcp_server,
        employee=EMPLOYEE_ID,
        record_type="training",
        values={
            "Training name": "training",
            "Status": "completed",
            "Frequency": "yearly",
            "Cost": {"value": 200, "currency": "GBP"},
        },
    )
    assert fake.posted == [
        (
            "training",
            {
                "name": "Training",
                "status": "Completed",
                "frequency": "Yearly",
                "cost": {"value": 200, "currency": "GBP"},
            },
        )
    ]


async def test_a_dependent_returns_its_entry_id(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="dependent",
            values={
                "First name": "Ada",
                "Surname": "Smith",
                "Birth date": "2015-04-01",
                "Gender": "female",
            },
        )
    )
    assert fake.posted[0][1] == {
        "firstName": "Ada",
        "surname": "Smith",
        "birthDate": "2015-04-01",
        "gender": "Female",
    }
    assert result["entry_id"] == fake.records_by_path["dependents"][0]["id"]
    assert result["verified"] is True


async def test_bank_details_are_sent_in_full_but_never_shown(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="bank account",
            values={
                "Bank name": "Acme",
                "Account number": "12345678",
                "IBAN": "GB29NWBK60161331926819",
                "Account type": "savings",
                "Use for bonus": "yes",
            },
        )
    )
    sent = fake.posted[0][1]
    assert sent["accountNumber"] == "12345678"
    assert sent["iban"] == "GB29NWBK60161331926819"
    assert sent["bankAccountType"] == "Savings"
    assert sent["useForBonus"] is True
    assert result["row"]["accountNumber"] == "****5678"
    assert result["row"]["iban"] == "******************6819"
    text = json.dumps(result)
    assert "12345678" not in text.replace("****5678", "")
    assert "GB29NWBK60161331926819" not in text


async def test_a_dropped_sensitive_column_is_reported_without_its_value(
    mock_api, mcp_server
):
    fake = FakePeople(mock_api)
    fake.drop_on_write["bank-accounts"] = {"accountNumber"}
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="bank account",
            values={"Bank name": "Acme", "Account number": "12345678"},
        )
    )
    assert result["unconfirmed"] == [
        {"field": "Bank account > accountNumber", "sent": "****5678", "read": None}
    ]
    assert "12345678" not in json.dumps(result).replace("****5678", "")


async def test_right_to_work_numbers_are_masked(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="right to work",
            values={
                "Document type": "Visa",
                "Document number": "AB1234567",
                "Expiration date": "2030-05-01",
            },
        )
    )
    assert fake.posted[0][1]["number"] == "AB1234567"
    assert result["row"]["number"] == "*****4567"


async def test_a_custom_table_is_added_by_name_with_its_mandatory_column(
    mock_api, mcp_server
):
    fake = FakePeople(mock_api)
    asked = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="certifications",
            values={"Expires": "2030-01-01"},
        )
    )
    assert asked["status"] == "needs_input"
    assert asked["questions"][0]["missing"][0]["column"] == "column_1"
    assert fake.writes == []
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="Certifications",
            values={"Certificate": "first aid", "Expires": "2030-01-01"},
        )
    )
    assert fake.posted == [
        ("about__table_1", {"values": [{"column_1": "C1", "column_2": "2030-01-01"}]})
    ]
    assert result["status"] == "added"
    assert result["verified"] is True


async def test_unknown_record_types_and_columns_are_refused_or_asked(
    mock_api, mcp_server
):
    fake = FakePeople(mock_api)
    text = await _add(
        mcp_server, employee=EMPLOYEE_ID, record_type="holidays", values={"x": 1}
    )
    assert text.startswith("Error:")
    assert "Variable pay" in text and "Certifications" in text
    asked = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="training",
            values={"Training name": "Training", "Colour": "red"},
        )
    )
    assert asked["status"] == "needs_input"
    assert asked["questions"][0]["key"] == "Colour"
    assert fake.writes == []


async def test_an_ambiguous_employee_is_a_question(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee="Alex Lee",
            record_type="training",
            values={"Training name": "Training"},
        )
    )
    assert result["status"] == "needs_input"
    assert result["questions"][0]["argument"] == "employee"
    assert fake.writes == []


async def test_a_bare_amount_asks_for_its_currency(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="training",
            values={"Training name": "Training", "Cost": 200},
        )
    )
    assert result["status"] == "needs_input"
    assert "currency" in result["questions"][0]["question"]
    assert fake.writes == []


async def test_a_column_given_twice_is_refused(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    text = await _add(
        mcp_server,
        employee=EMPLOYEE_ID,
        record_type="training",
        values={"Training name": "Training", "name": "Training"},
    )
    assert text.startswith("Error:") and "given twice" in text
    assert fake.writes == []


async def test_a_column_hibob_drops_is_reported_unconfirmed(
    mock_api, mcp_server, recorded_sleeps
):
    fake = FakePeople(mock_api)
    fake.drop_on_write["training"] = {"status"}
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="training",
            values={"Training name": "Training", "Status": "completed"},
        )
    )
    assert result["status"] == "added"
    assert result["unconfirmed"] == [
        {"field": "Training > status", "sent": "Completed", "read": None}
    ]
    assert "verified" not in result or result["verified"] is False
    assert recorded_sleeps == [1.0, 3.0, 6.0]


async def test_a_denied_write_names_the_table_to_grant(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    fake.row_status["training"] = 403
    text = await _add(
        mcp_server,
        employee=EMPLOYEE_ID,
        record_type="training",
        values={"Training name": "Training"},
    )
    assert text.startswith("Error:")
    assert "Training" in text and "Edit" in text


async def test_the_tool_is_absent_in_read_only_mode(server_factory):
    with pytest.raises(ToolError, match="Unknown tool"):
        await call_tool(
            server_factory(read_only=True),
            "hibob_add_employee_record",
            {"employee": EMPLOYEE_ID, "record_type": "training", "values": {"x": 1}},
        )
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_tools_employee_records.py tests/test_read_only_gating.py -q 2>&1 | tail -6`
Expected: failures with `Unknown tool: hibob_add_employee_record`.

- [ ] **Step 3: Make the value resolver public**

In `src/hibob_advanced_mcp/employee_updates.py`, rename `_resolve_value` to `resolve_value` (its definition and its one call in `_plan`).

- [ ] **Step 4: Implement the tool**

Create `src/hibob_advanced_mcp/employee_record_adds.py`:

```python
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
                            "question": f"Which employee did you mean by {str(employee)!r}?",
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
                            "question": f"Which {rt.label} column did you mean by {key!r}?",
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
                        "question": f"From what date should the {rt.label} record for {who} apply?",
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
```

In `src/hibob_advanced_mcp/employees.py`: add `from .employee_record_adds import register_record_tools`, and at the very end of `register_employee_tools` (after `register_update_tools(...)`), add:

```python
    register_record_tools(
        mcp, client_factory=client_factory, cache=cache, sleep=sleep or asyncio.sleep
    )
```

(`register_employee_tools` returns early when `read_only`, before this line, so the tool is absent in read-only mode.)

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest -q 2>&1 | tail -5`
Expected: all pass. Fix the code, not the tests, if an assertion fails, unless it contradicts the spec.

- [ ] **Step 6: Update the README**

In the README: add the write-table row

`| \`hibob_add_employee_record\` | \`POST /people/{id}/<table>\` (or \`POST /people/custom-tables/{id}/{table}\`), sent once; the type's rows are read first and again afterwards | 10/min |`

change "the twelve write tools" to "the thirteen write tools", and add after the `hibob_update_employee` paragraph:

```
`hibob_add_employee_record` adds one row to the tables HiBob keeps several rows in: variable pay, entitlements and deductions (dated: the tool asks for the effective date), equity grants, training, bank accounts, dependents, right-to-work documents, and the company's custom tables. A new record copies nothing, so the columns it needs are asked for, with the options a list offers; a value matching nothing or several things comes back as a question; the type's list, column and date rules are in `hibob_list_employee_fields` under `record_types`. Entitlement and deduction types are sent by name, as HiBob wants. Calling again with the same values adds nothing (the identical record is reported), so a retry after a lost response is safe. Bank account, IBAN, routing and document numbers are sent in full and shown back masked to their last four characters, including in `hibob_get_employee`'s `history`. Every record is read back and compared; a dropped column is listed under `unconfirmed`. HiBob's API has no way to edit or delete a record through this server. Bank accounts and right-to-work records need the sensitive-data write permission in HiBob.
```

- [ ] **Step 7: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy
uvx --no-cache --from . hibob-advanced-mcp --test 2>&1 | grep "Registered tools"
git add -A src tests README.md
git commit -q -m "Add hibob_add_employee_record

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
Expected: `Registered tools (33):`.

---

### Task 4: Check it live (not bank accounts or right-to-work), and record the findings

**Files:**
- Modify: `tests/fixtures/people/observations.md`, the spec, the memory note

- [ ] **Step 1: Run the harness**

Reuse the harness from the phase 3 plan (Task 5, Step 1; the scratch copy is usually still there). Run each calls file with `live.sh`.

- [ ] **Step 2: Entitlement, deduction, variable pay**

`calls_r1.json`:

```json
[["hibob_add_employee_record", {"employee": "david@harriethq.com", "record_type": "Entitlement", "values": {"Entitlement type": "Lunch vouchers", "Amount": {"value": 1, "currency": "GBP"}}}],
 ["hibob_add_employee_record", {"employee": "david@harriethq.com", "record_type": "Entitlement", "values": {"Entitlement type": "Lunch vouchers", "Amount": {"value": 1, "currency": "GBP"}}, "effective_date": "2030-01-01"}],
 ["hibob_add_employee_record", {"employee": "david@harriethq.com", "record_type": "Entitlement", "values": {"Entitlement type": "Lunch vouchers", "Amount": {"value": 1, "currency": "GBP"}}, "effective_date": "2030-01-01"}],
 ["hibob_add_employee_record", {"employee": "david@harriethq.com", "record_type": "Deduction", "values": {"Deduction type": "Cycle to work", "Amount": {"value": 1, "currency": "GBP"}}, "effective_date": "2030-01-01"}],
 ["hibob_add_employee_record", {"employee": "david@harriethq.com", "record_type": "Deduction", "values": {"Deduction type": "Cycle to work", "Amount": {"value": 2, "currency": "GBP"}}, "effective_date": "2030-01-01"}],
 ["hibob_add_employee_record", {"employee": "david@harriethq.com", "record_type": "Variable pay", "values": {"Variable type": "Bonus", "Amount": {"value": 1, "currency": "GBP"}, "Payment period": "Annual"}, "effective_date": "2030-01-01"}]]
```

Expected: (1) `needs_input` for the date; (2) `added` with an `entry_id`, `verified: true`; (3) `unchanged` (the identical-record guard, nothing posted); (4) `added`; (5) `Error:` with HiBob's duplicate message and the "only adds rows" hint (HiBob refuses a deduction with the same date and type); (6) `added`, or a 400 whose message shows how HiBob wants `variableType` (the list behind it is unconfirmed: `payType` is a guess from the field metadata) — fix the column's list or value form and record a ruling if it is rejected.

- [ ] **Step 3: Equity, training, and a read of everything**

`calls_r2.json`:

```json
[["hibob_add_employee_record", {"employee": "david@harriethq.com", "record_type": "Bank account", "values": {"Bank name": "API check", "Account nickname": "API check", "Account type": "Savings"}}],
 ["hibob_add_employee_record", {"employee": "david@harriethq.com", "record_type": "Equity grant", "values": {"Quantity": 1, "Equity type": "API check", "Grant type": "Merit Grant", "Grant date": "2026-10-07"}}],
 ["hibob_add_employee_record", {"employee": "david@harriethq.com", "record_type": "Training", "values": {"Training name": "Training", "Status": "Invited", "Frequency": "Once", "Description": "API check"}}],
 ["hibob_get_employee", {"employee": "david@harriethq.com", "fields": ["Job title"], "history": ["entitlement", "deduction", "variable pay", "equity", "training", "bank accounts"]}]]
```

Expected: the bank account is `added` and `verified: true`, or HiBob refuses a bank account with no account number (a 400 naming what is missing: record it and leave the numbered path to the user); equity and training are `added` and `verified: true` (they answer 200 with no body, so the row is found by diffing the table); the history shows the new rows. Note any column HiBob rejects (equity's required columns and the training required column are only partly documented) and any `unconfirmed`.

- [ ] **Step 4: Record what was found**

Append a "Records (live)" section to `tests/fixtures/people/observations.md`: which calls worked, the list behind variable type, whether entitlement/deduction uniqueness matched, the shape of the rows read back, and the IDs of every row added on `david@harriethq.com` (nothing is deleted). State plainly that no account, IBAN, routing or document number was written, that right-to-work records, dependents and custom tables were **not** written live, and that the bank-account result shows only non-sensitive columns, so those bodies are as documented. Add the same to the spec's findings and update the memory note (phase 4 shipped; rows added; what is unverified).

- [ ] **Step 5: Full check and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy && .venv/bin/pytest -q 2>&1 | tail -2
git add -A tests/fixtures/people/observations.md docs
git commit -q -m "Record the live behaviour of record writes

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Tell the user what was left**

List every record added to `david@harriethq.com` (IDs and dates), say none was deleted, say no bank or document number was written, that right-to-work, dependents and custom tables were not written live, and give the user the exact call to test a numbered bank account themselves, and give the final tool counts.
