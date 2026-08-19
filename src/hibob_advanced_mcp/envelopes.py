"""Translation between flat field dictionaries and HiBob's wire format.

HiBob wraps every field value as ``{"value": ...}`` and every write body in an
``{"items": [{"objectType": ..., "fields": {...}}]}`` envelope. Callers of this
server work with flat dictionaries instead (``{"/position/fte": 100}``), and
these pure functions do the conversion in both directions.
"""

from __future__ import annotations

from typing import Any

OBJECT_TYPE_POSITION = "position"
OBJECT_TYPE_OPENING = "positionOpening"
OBJECT_TYPE_BUDGET = "positionBudget"

OBJECT_TYPES = (OBJECT_TYPE_POSITION, OBJECT_TYPE_OPENING, OBJECT_TYPE_BUDGET)

# Nested objects accepted inside a position create payload.
NESTED_POSITION_OPENING_KEY = "/position/positionOpening"
NESTED_POSITION_BUDGET_KEY = "/position/positionBudget"


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


def _wrap_value(value: Any) -> dict[str, Any]:
    """Wrap a raw value as ``{"value": ...}``, passing through pre-wrapped ones."""
    if isinstance(value, dict) and set(value.keys()) <= {"value", "humanReadable"}:
        if "value" in value:
            return {"value": value["value"]}
    return {"value": value}


def wrap_fields(object_type: str, flat: dict[str, Any]) -> dict[str, Any]:
    """Convert ``{"/position/fte": 100}`` into ``{"/position/fte": {"value": 100}}``."""
    if not isinstance(flat, dict):
        raise ValueError(
            f"Expected a dictionary of {object_type} fields, got {type(flat).__name__}."
        )
    wrapped: dict[str, Any] = {}
    for key, value in flat.items():
        wrapped[normalize_field_key(object_type, key)] = _wrap_value(value)
    return wrapped


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
    """Flatten a list of search result entries."""
    if not isinstance(entries, list):
        return []
    return [flatten_search_entry(entry) for entry in entries if isinstance(entry, dict)]
