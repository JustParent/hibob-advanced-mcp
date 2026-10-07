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
