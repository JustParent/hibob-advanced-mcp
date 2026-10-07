"""Rows of HiBob's effective-dated tables: work, employment and salary.

HiBob replaces a table row wholesale: a column left out of a write is stored
empty. So a change to one column is written as a new row that copies the
row in force before its date and lays the change over it. These pure
functions choose that row, build the new one, and compare what HiBob holds
afterwards with what was sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .employee_rows import same_value

CUSTOM_PREFIX = "customColumns."


@dataclass(frozen=True)
class TableSpec:
    key: str
    label: str
    path: str
    reason_column: str | None


TABLES = {
    "work": TableSpec("work", "work", "work", "reason"),
    "employment": TableSpec("employment", "employment", "employment", "reason"),
    "salary": TableSpec("salary", "salary", "salaries", None),
}
TABLE_ORDER = ("work", "employment", "salary")

# Read-only or bookkeeping keys of a row, which a new row never carries.
BOOKKEEPING_KEYS = frozenset(
    {
        "id",
        "effectiveDate",
        "endEffectiveDate",
        "activeEffectiveDate",
        "isCurrent",
        "canBeDeleted",
        "change",
        "creationDate",
        "CreationDate",
        "modificationDate",
        "workChangeType",
        "humanReadable",
        "changedBy",
    }
)
# A label HiBob shows beside the ID it is written as: only the ID is sent.
LABEL_BESIDE_ID = {
    "site": "siteId",
    "calendarName": "calendarId",
    "standardWorkingPattern": "standardWorkingPatternId",
}
# Columns HiBob works out from others: copied as read, never compared after.
DERIVED_COLUMNS = frozenset(
    {
        "fte",
        "weeklyHours",
        "hoursInDayNotWorked",
        "actualWorkingPattern",
        "siteWorkingPattern",
        *LABEL_BESIDE_ID,
    }
)
NOT_COMPARED = DERIVED_COLUMNS | {"effectiveDate", "reason"}
# The column holding the name of an ID column, for showing people.
DISPLAY_COLUMN = {"siteId": "site", "calendarId": "calendarName"}


def to_wire(wire: str, value: Any, label: str) -> Any:
    """``value`` as a table's write body takes it."""
    if wire == "int":
        text = str(value).strip()
        if not text.isdigit():
            raise ValueError(f"{label} needs a numeric list item ID, not {value!r}.")
        return int(text)
    if wire == "employee":
        return {"id": str(value)}
    return value


def restricted_message(table: TableSpec, restricted: Any) -> str | None:
    """Why a table read cannot be trusted, or None.

    HiBob lists under restricted_columns the columns the service user may not
    see, or may not see the history of.
    """
    if not isinstance(restricted, dict):
        return None
    needs = {"no_view_permission": "View", "no_view_history_permission": "View history"}
    parts = [
        f"{needs.get(kind, kind)} on {', '.join(str(c) for c in columns)}"
        for kind, columns in restricted.items()
        if isinstance(columns, list) and columns
    ]
    if not parts:
        return None
    return (
        f"HiBob hides part of the {table.label} table from the service user, and a "
        "new row built from a partial read would blank what it cannot see. Grant "
        "People's data > People's fields: " + "; ".join(parts) + "."
    )


def split_rows(
    rows: list[dict[str, Any]], day: str, table: TableSpec
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    """(base, same_day, later) for a new row dated ``day``.

    The base is the latest row dated before it; the row marked current is not
    used, because a row may be added in the past or the future. ``later`` is
    every row dated after it, earliest first.
    """
    dated = [row for row in rows if isinstance(row.get("effectiveDate"), str)]
    same = next((row for row in dated if row["effectiveDate"] == day), None)
    earlier = [row for row in dated if row["effectiveDate"] < day]
    later = sorted(
        (row for row in dated if row["effectiveDate"] > day),
        key=lambda row: row["effectiveDate"],
    )
    base = None
    if earlier:
        newest = max(row["effectiveDate"] for row in earlier)
        tied = [row for row in earlier if row["effectiveDate"] == newest]
        if len(tied) > 1:
            raise ValueError(
                f"Two {table.label} rows are dated {newest}, so there is no single "
                "row to copy."
            )
        base = tied[0]
    return base, same, later


def held_value(row: dict[str, Any] | None, column: str) -> Any:
    """What a row stores in a column, as HiBob returns it."""
    if not row:
        return None
    if column.startswith(CUSTOM_PREFIX):
        return (row.get("customColumns") or {}).get(column[len(CUSTOM_PREFIX) :])
    return row.get(column)


def row_value(row: dict[str, Any] | None, column: str) -> Any:
    """What a row holds in a column, as a person would read it."""
    if not row:
        return None
    readable = row.get("humanReadable")
    if isinstance(readable, dict) and readable.get(column) not in (None, ""):
        return readable[column]
    value = held_value(row, column)
    if isinstance(value, dict) and value.get("displayName"):
        return value["displayName"]
    name_column = DISPLAY_COLUMN.get(column)
    if name_column and row.get(name_column):
        return row[name_column]
    return value


def build_row(
    base: dict[str, Any] | None,
    changes: dict[str, Any],
    day: str,
    table: TableSpec,
    reason: str | None = None,
) -> dict[str, Any]:
    """The body of a new row: the base row's columns with ``changes`` laid over.

    Null columns are left out (HiBob stores an omitted column as empty, which
    is the same). Custom columns are sent in both shapes HiBob is known to
    take: nested, as its reference documents, and as top-level keys, as
    justparent's integration sends them.
    """
    body: dict[str, Any] = {}
    custom: dict[str, Any] = {}
    for key, value in (base or {}).items():
        if key in BOOKKEEPING_KEYS or value is None or value == {}:
            continue
        if key == "customColumns":
            if isinstance(value, dict):
                custom.update(value)
        elif key == "reportsTo" and isinstance(value, dict) and "id" in value:
            body[key] = {"id": str(value["id"])}
        else:
            body[key] = value
    for column, value in changes.items():
        if column.startswith(CUSTOM_PREFIX):
            custom[column[len(CUSTOM_PREFIX) :]] = value
        else:
            body[column] = value
    for label, identifier in LABEL_BESIDE_ID.items():
        if identifier in body:
            body.pop(label, None)
    if custom:
        body["customColumns"] = custom
        for column, value in custom.items():
            body.setdefault(column, value)
    body["effectiveDate"] = day
    if reason and table.reason_column:
        body[table.reason_column] = reason
    return body


def later_conflicts(
    later: list[dict[str, Any]], changes: dict[str, Any]
) -> list[dict[str, Any]]:
    """The later rows that still hold a different value in a changed column."""
    found: list[dict[str, Any]] = []
    for row in later:
        differing = {
            column: row_value(row, column)
            for column, value in changes.items()
            if not same_value(value, held_value(row, column))
        }
        if differing:
            found.append({"effectiveDate": row["effectiveDate"], "columns": differing})
    return found


def compare_row(body: dict[str, Any], row: dict[str, Any]) -> list[dict[str, Any]]:
    """The columns of ``row`` (as HiBob holds it) that differ from ``body``."""
    custom = body.get("customColumns")
    custom = custom if isinstance(custom, dict) else {}
    problems: list[dict[str, Any]] = []
    for column, value in body.items():
        if column in NOT_COMPARED or column in custom:
            continue
        if column == "customColumns":
            for name, sent in custom.items():
                held = (row.get("customColumns") or {}).get(name)
                if not same_value(sent, held):
                    problems.append(
                        {
                            "column": f"{CUSTOM_PREFIX}{name}",
                            "sent": sent,
                            "read": held,
                        }
                    )
            continue
        held = row.get(column)
        if not same_value(value, held):
            problems.append({"column": column, "sent": value, "read": held})
    return problems
