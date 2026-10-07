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

LIST_TYPES = frozenset({"list", "multi-list", "hierarchy-list", "list_id"})
EMPLOYEE_TYPES = frozenset({"employee-reference", "employee"})
TEXT_TYPES = frozenset({"text", "text-area", "time", "ssn"})
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
