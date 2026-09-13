"""Position and opening references: by ID or by the name HiBob shows.

A caller can name a position as HiBob displays it (``P-0000000368``) rather
than by its numeric ID, and an opening likewise (``O-6853240227``). Digits are
an ID; anything else is a name, looked up through the search endpoint's
name filter. A budget has no name, but it does not need one: the position
carries its budget's ID as ``/position/budget``, so a budget write can be
addressed by position alone and checked against it.

These are the pure parts: classifying a reference, building its filter,
shaping the row that comes back and the cross-checks that refuse a write to
the wrong record. The lookups themselves live with the tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .envelopes import normalize_id

POSITION_ID_FIELD = "/position/id"
POSITION_NAME_FIELD = "/position/name"
POSITION_BUDGET_FIELD = "/position/budget"
OPENING_ID_FIELD = "/positionOpening/id"
OPENING_NAME_FIELD = "/positionOpening/positionOpeningName"
OPENING_POSITION_ID_FIELD = "/positionOpening/positionId"

# Enough to identify a position and address its budget.
POSITION_REF_FIELDS = (POSITION_ID_FIELD, POSITION_NAME_FIELD, POSITION_BUDGET_FIELD)
# Enough to identify an opening and check which position it belongs to.
OPENING_REF_FIELDS = (OPENING_ID_FIELD, OPENING_NAME_FIELD, OPENING_POSITION_ID_FIELD)

NOTHING_WRITTEN = "Nothing was written."


def _clean(value: Any) -> str:
    text = normalize_id(value) if value is not None else ""
    if not text:
        raise ValueError("Position and opening references cannot be empty.")
    return text


def is_numeric_id(value: Any) -> bool:
    """Whether ``value`` is an ID rather than a name: digits and nothing else."""
    if value is None:
        return False
    return normalize_id(value).isdigit()


def reference_filter(value: Any, *, id_field: str, name_field: str) -> dict[str, Any]:
    """The search filter that picks out ``value``, by ID or by name."""
    text = _clean(value)
    field = id_field if text.isdigit() else name_field
    return {"fieldId": field, "operator": "equals", "values": [text]}


def split_references(values: list[Any]) -> tuple[list[str], list[str]]:
    """Separate a list of references into IDs and names, each deduplicated."""
    ids: list[str] = []
    names: list[str] = []
    for raw in values:
        text = _clean(raw)
        bucket = ids if text.isdigit() else names
        if text not in bucket:
            bucket.append(text)
    return ids, names


def _cell(row: dict[str, Any], field: str) -> Any:
    cell = row.get(field)
    return cell.get("value") if isinstance(cell, dict) else cell


def _id_or_none(value: Any) -> str | None:
    return None if value is None else normalize_id(value)


def single_match(
    rows: list[dict[str, Any]], what: str, value: str, *, id_field: str
) -> dict[str, Any]:
    """The one row a reference picks out; anything else is refused.

    Several rows means a name HiBob has let two records share, and picking
    the first would write to whichever happened to sort first.
    """
    if len(rows) == 1:
        return rows[0]
    if not rows:
        how = f"with ID {value!r}" if value.isdigit() else f"named {value!r}"
        raise ValueError(f"No {what} {how} in HiBob.")
    ids = ", ".join(normalize_id(_cell(row, id_field)) for row in rows)
    raise ValueError(
        f"{value!r} names {len(rows)} {what}s (IDs {ids}); pass the numeric ID instead."
    )


@dataclass(frozen=True)
class PositionRef:
    """A position as addressed in a write, with the budget it carries."""

    id: str
    name: str | None
    budget_id: str | None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> PositionRef:
        name = _cell(row, POSITION_NAME_FIELD)
        return cls(
            id=normalize_id(_cell(row, POSITION_ID_FIELD)),
            name=None if name is None else str(name),
            budget_id=_id_or_none(_cell(row, POSITION_BUDGET_FIELD)),
        )

    def describe(self) -> str:
        if self.name:
            return f"position {self.name} (ID {self.id})"
        return f"position {self.id}"


@dataclass(frozen=True)
class OpeningRef:
    """An opening as addressed in a write, with the position it belongs to."""

    id: str
    name: str | None
    position_id: str | None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> OpeningRef:
        name = _cell(row, OPENING_NAME_FIELD)
        return cls(
            id=normalize_id(_cell(row, OPENING_ID_FIELD)),
            name=None if name is None else str(name),
            position_id=_id_or_none(_cell(row, OPENING_POSITION_ID_FIELD)),
        )

    def describe(self) -> str:
        if self.name:
            return f"opening {self.name} (ID {self.id})"
        return f"opening {self.id}"


def check_opening_parent(opening: OpeningRef, position: PositionRef) -> None:
    """Refuse an opening write addressed under a position it does not belong to."""
    if opening.position_id == position.id:
        return
    raise ValueError(
        f"{opening.describe()} belongs to position {opening.position_id}, not "
        f"{position.describe()}. {NOTHING_WRITTEN}"
    )


def budget_to_write(position: PositionRef, budget_id: Any) -> str:
    """The budget a write on ``position`` should address.

    With no budget given, the position's own; with one given, it must be the
    position's own, so a budget ID carried over from another position cannot
    be written under the wrong parent.
    """
    if position.budget_id is None:
        raise ValueError(
            f"{position.describe()} has no budget to update; create one with "
            "hibob_create_position_budget."
        )
    if budget_id is None or not normalize_id(budget_id):
        return position.budget_id
    given = normalize_id(budget_id)
    if given != position.budget_id:
        raise ValueError(
            f"Budget {given} does not belong to {position.describe()}, whose "
            f"budget is {position.budget_id}. {NOTHING_WRITTEN}"
        )
    return given


def refuse_existing_budget(position: PositionRef) -> None:
    """Refuse to create a second budget: HiBob allows one per position."""
    if position.budget_id is not None:
        raise ValueError(
            f"{position.describe()} already has budget {position.budget_id}; change "
            "it with hibob_update_position_budget."
        )
