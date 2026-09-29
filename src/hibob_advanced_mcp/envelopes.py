"""Translation between flat field dictionaries and HiBob's wire format.

HiBob wraps every field value as ``{"value": ...}`` and every write body in an
``{"items": [{"objectType": ..., "fields": {...}}]}`` envelope. Callers of this
server work with flat dictionaries instead (``{"/position/fte": 100}``), and
these pure functions do the conversion in both directions.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

OBJECT_TYPE_POSITION = "position"
OBJECT_TYPE_OPENING = "positionOpening"
OBJECT_TYPE_BUDGET = "positionBudget"

OBJECT_TYPES = (OBJECT_TYPE_POSITION, OBJECT_TYPE_OPENING, OBJECT_TYPE_BUDGET)

# Nested objects accepted inside a position create payload.
NESTED_POSITION_OPENING_KEY = "/position/positionOpening"
NESTED_POSITION_BUDGET_KEY = "/position/positionBudget"

# How HiBob's API reference types each documented write field. HiBob refuses
# a value of any other JSON type, with a bare 400 at least for a string ID, so
# a value is converted where that is unambiguous and refused otherwise before
# anything is sent. Null is refused too: HiBob allows it only on some creates,
# where leaving the field out does the same, and never on update.
#
# List fields whose item IDs HiBob takes as numbers. A caller can easily hold
# one as a string (hibob_resolve_list_values returns every ID as a string).
NUMERIC_ID_FIELDS = frozenset(
    {"/position/site", "/position/jobProfile", "/position/managerPositionId"}
)
NUMBER_FIELDS = frozenset({"/position/fte"})
# Budget searches return these as {"value": n, "currency": c}; writes take
# the bare number, in the currency the budget names.
AMOUNT_FIELDS = frozenset(
    {
        "/positionBudget/expectedBaseSalaryCurrencyValue",
        "/positionBudget/totalPositionCostCurrencyValue",
        "/positionBudget/expectedVariablePayCurrencyValue",
    }
)
BUDGET_CURRENCY_FIELD = "/positionBudget/currency"
# Searches display dates day first (01/09/2026); writes take YYYY-MM-DD.
DATE_FIELDS = frozenset(
    {"/position/effectiveDate", "/positionOpening/expectedStartDate"}
)
# List fields whose item IDs HiBob takes as strings, numeric-looking or not.
STRING_FIELDS = frozenset(
    {
        "/position/department",
        "/position/positionType",
        "/position/employmentType",
        "/positionOpening/recruitmentStatus",
        BUDGET_CURRENCY_FIELD,
        "/positionBudget/salaryPayPeriod",
        "/positionBudget/variablePayPeriod",
    }
)

_WHOLE_NUMBER = re.compile(r"[0-9]+")
_DECIMAL = re.compile(r"[0-9]+(\.[0-9]+)?")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def normalize_field_key(object_type: str, key: str) -> str:
    """Normalize a field key to HiBob's ``/objectType/name`` form.

    Accepts ``"fte"``, ``"position/fte"`` and ``"/position/fte"`` alike. Keys
    that carry a different object type's prefix are rejected, because silently
    re-prefixing them would send the wrong field to HiBob.
    """
    raw = (key or "").strip()
    if not raw:
        raise ValueError("Field names cannot be empty.")

    trimmed = raw.strip("/")
    parts = [p for p in trimmed.split("/") if p]
    if not parts:
        raise ValueError(f"Invalid field name: {key!r}")

    if len(parts) == 1:
        return f"/{object_type}/{parts[0]}"

    prefix, name = parts[0], "/".join(parts[1:])
    if prefix != object_type:
        if prefix in OBJECT_TYPES:
            raise ValueError(
                f"Field {key!r} belongs to {prefix!r}, but a {object_type!r} field "
                f"was expected. Use '/{object_type}/{name}' or pass it in the "
                f"{prefix} argument instead."
            )
        raise ValueError(
            f"Unrecognized field prefix {prefix!r} in {key!r}. Expected a "
            f"{object_type!r} field such as '/{object_type}/...'."
        )
    return f"/{object_type}/{name}"


def normalize_id(value: Any) -> str:
    """Render an ID the way HiBob's JSON does, so ints and strings compare."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _wrap_value(value: Any) -> dict[str, Any]:
    """Wrap a raw value as ``{"value": ...}``, passing through pre-wrapped ones."""
    if (
        isinstance(value, dict)
        and set(value.keys()) <= {"value", "humanReadable"}
        and "value" in value
    ):
        return {"value": value["value"]}
    return {"value": value}


def _refused(field_id: str, value: Any, wanted: str, hint: str = "") -> ValueError:
    if value is None:
        return ValueError(f"{field_id} cannot be null; leave the field out instead.")
    return ValueError(f"{field_id} must be {wanted}, not {value!r}.{hint}")


def _numeric_id(field_id: str, value: Any) -> int:
    """A list item ID as the number HiBob expects, from a number or digits."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and _WHOLE_NUMBER.fullmatch(value.strip()):
        return int(value.strip())
    raise _refused(
        field_id,
        value,
        "the list item's numeric ID",
        " Look the ID up with hibob_resolve_list_values.",
    )


def _number(field_id: str, value: Any, wanted: str) -> int | float:
    """A number, from a number or a plain decimal such as "65000.5"."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and _DECIMAL.fullmatch(text := value.strip()):
        return float(text) if "." in text else int(text)
    raise _refused(field_id, value, wanted)


def _amount(field_id: str, value: Any, budget_currency: Any) -> int | float:
    """A budget amount, from a number or a money value in the budget's currency."""
    if isinstance(value, dict) and set(value) == {"value", "currency"}:
        currency = value["currency"]
        if budget_currency is None:
            raise ValueError(
                f"{field_id} is given in {currency!r}, but this write does not name "
                f"{BUDGET_CURRENCY_FIELD} to check that against. Send a plain number "
                f"in the budget's currency, or include {BUDGET_CURRENCY_FIELD}."
            )
        if normalize_id(currency) != normalize_id(budget_currency):
            raise ValueError(
                f"{field_id} is given in {currency!r}, but the budget's currency is "
                f"{budget_currency!r}. Send the amount in {budget_currency!r}."
            )
        value = value["value"]
    return _number(field_id, value, "a plain number in the budget's currency")


def _iso_date(field_id: str, value: Any) -> str:
    """A date as HiBob's writes take it: YYYY-MM-DD, and a real day."""
    if isinstance(value, str) and _ISO_DATE.fullmatch(text := value.strip()):
        try:
            date.fromisoformat(text)
        except ValueError:
            pass
        else:
            return text
    raise _refused(field_id, value, "a date written YYYY-MM-DD")


def _list_string(field_id: str, value: Any) -> str:
    """A list item ID as the string HiBob expects, from a string or a number."""
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    raise _refused(field_id, value, "a string, such as a list item ID")


def _typed_cell(field_id: str, value: Any, budget_currency: Any) -> dict[str, Any]:
    """Wrap one value, in the JSON type HiBob's reference gives its field."""
    cell = _wrap_value(value)
    raw = cell["value"]
    if field_id in NUMERIC_ID_FIELDS:
        return {"value": _numeric_id(field_id, raw)}
    if field_id in NUMBER_FIELDS:
        return {"value": _number(field_id, raw, "a number, where 100 is full time")}
    if field_id in AMOUNT_FIELDS:
        return {"value": _amount(field_id, raw, budget_currency)}
    if field_id in DATE_FIELDS:
        return {"value": _iso_date(field_id, raw)}
    if field_id in STRING_FIELDS:
        return {"value": _list_string(field_id, raw)}
    return cell


def wrap_fields(object_type: str, flat: dict[str, Any]) -> dict[str, Any]:
    """Convert ``{"/position/fte": 100}`` into ``{"/position/fte": {"value": 100}}``.

    Each documented field's value is sent as the JSON type HiBob's reference
    gives it, converted where that is unambiguous; anything else is refused
    before it reaches HiBob.
    """
    if not isinstance(flat, dict):
        raise ValueError(
            f"Expected a dictionary of {object_type} fields, got {type(flat).__name__}."
        )
    fields = {
        normalize_field_key(object_type, key): value for key, value in flat.items()
    }
    budget_currency = fields.get(BUDGET_CURRENCY_FIELD)
    if budget_currency is not None:
        budget_currency = _wrap_value(budget_currency)["value"]
    return {
        field_id: _typed_cell(field_id, value, budget_currency)
        for field_id, value in fields.items()
    }


def _nested_object(object_type: str, flat: dict[str, Any]) -> dict[str, Any]:
    return {"objectType": object_type, "fields": wrap_fields(object_type, flat)}


def build_items_envelope(
    object_type: str,
    flat: dict[str, Any],
    *,
    opening: dict[str, Any] | None = None,
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the ``{"items": [...]}`` write envelope for a single object.

    ``opening`` and ``budget`` are only valid alongside a position and are
    nested under their reserved field keys.
    """
    fields = wrap_fields(object_type, flat)

    if opening is not None:
        if object_type != OBJECT_TYPE_POSITION:
            raise ValueError("Position openings can only be nested inside a position.")
        fields[NESTED_POSITION_OPENING_KEY] = _nested_object(
            OBJECT_TYPE_OPENING, opening
        )
    if budget is not None:
        if object_type != OBJECT_TYPE_POSITION:
            raise ValueError("Position budgets can only be nested inside a position.")
        fields[NESTED_POSITION_BUDGET_KEY] = _nested_object(OBJECT_TYPE_BUDGET, budget)

    return {"items": [{"objectType": object_type, "fields": fields}]}


def validate_required_keys(
    object_type: str, flat: dict[str, Any], required: set[str]
) -> None:
    """Raise if any required field is missing, naming the normalized keys.

    Validating before the request avoids spending a call from HiBob's small
    write rate budget on a payload that cannot succeed.
    """
    present = {normalize_field_key(object_type, key) for key in flat}
    missing = sorted(
        normalize_field_key(object_type, key)
        for key in required
        if normalize_field_key(object_type, key) not in present
    )
    if missing:
        raise ValueError(
            f"Missing required {object_type} field(s): {', '.join(missing)}. "
            "Use hibob_list_workforce_fields to see every available field."
        )


def validate_allowed_keys(
    object_type: str, flat: dict[str, Any], allowed: set[str]
) -> None:
    """Raise if a field cannot be written on this object type."""
    allowed_normalized = {normalize_field_key(object_type, key) for key in allowed}
    unknown = sorted(
        normalize_field_key(object_type, key)
        for key in flat
        if normalize_field_key(object_type, key) not in allowed_normalized
    )
    if unknown:
        raise ValueError(
            f"Field(s) not updatable on {object_type}: {', '.join(unknown)}. "
            f"Updatable fields are: {', '.join(sorted(allowed_normalized))}."
        )


def flatten_search_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Flatten one search result entry.

    HiBob returns ``{fieldId: {"value": ..., "humanReadable": ...}}``. Raw
    values are kept because later update calls need the underlying IDs, while
    the human-readable labels are surfaced separately for display.
    """
    values: dict[str, Any] = {}
    display: dict[str, Any] = {}
    for field_id, cell in (entry or {}).items():
        if isinstance(cell, dict) and ("value" in cell or "humanReadable" in cell):
            if "value" in cell:
                values[field_id] = cell["value"]
            human = cell.get("humanReadable")
            if human is not None:
                display[field_id] = human
        else:
            values[field_id] = cell

    flattened: dict[str, Any] = {"values": values}
    if display:
        flattened["display"] = display
    return flattened


def flatten_search_entries(entries: Any) -> list[dict[str, Any]]:
    """Flatten a list of search result entries.

    HiBob's API reference declares the opening and budget entries as a list
    of lists, so one level of nesting is unwrapped as well.
    """
    if not isinstance(entries, list):
        return []
    flattened: list[dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, dict):
            flattened.append(flatten_search_entry(entry))
        elif isinstance(entry, list):
            flattened.extend(
                flatten_search_entry(inner)
                for inner in entry
                if isinstance(inner, dict)
            )
    return flattened
