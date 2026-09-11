"""Assemble a fill-in form for a workforce planning object.

HiBob spreads what a caller needs to create a position across several
endpoints: the metadata endpoints list an object's fields and say which named
list each list field draws from, the named-lists endpoint holds the list
items, and the API reference documents which fields are required, which are
writable, and the fixed vocabularies of fields that are not list-backed.

The pure functions here join those sources into one structure per object,
so a caller can render an accurate form (with the right drop-down options)
from a single tool result. Network calls stay in ``workforce_planning``.
"""

from __future__ import annotations

from typing import Any

from .envelopes import (
    OBJECT_TYPE_BUDGET,
    OBJECT_TYPE_OPENING,
    OBJECT_TYPE_POSITION,
    normalize_field_key,
)

# Fields HiBob requires when creating each object.
REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    OBJECT_TYPE_POSITION: frozenset(
        {
            "/position/effectiveDate",
            "/position/fte",
            "/position/department",
            "/position/site",
            "/position/jobProfile",
        }
    ),
    OBJECT_TYPE_OPENING: frozenset({"/positionOpening/expectedStartDate"}),
    OBJECT_TYPE_BUDGET: frozenset(
        {"/positionBudget/salaryPayPeriod", "/positionBudget/currency"}
    ),
}

# Fields HiBob's API reference accepts in a create (and update) payload, in
# the order the reference lists them. Anything else the metadata reports is
# either calculated by HiBob or a custom field.
WRITABLE_FIELDS: dict[str, tuple[str, ...]] = {
    OBJECT_TYPE_POSITION: (
        "/position/name",
        "/position/effectiveDate",
        "/position/managerPositionId",
        "/position/positionType",
        "/position/fte",
        "/position/employmentType",
        "/position/department",
        "/position/site",
        "/position/jobProfile",
        "/position/reason",
    ),
    OBJECT_TYPE_OPENING: (
        "/positionOpening/positionOpeningName",
        "/positionOpening/expectedStartDate",
        "/positionOpening/recruitmentStatus",
    ),
    OBJECT_TYPE_BUDGET: (
        "/positionBudget/expectedBaseSalaryCurrencyValue",
        "/positionBudget/salaryPayPeriod",
        "/positionBudget/totalPositionCostCurrencyValue",
        "/positionBudget/currency",
        "/positionBudget/variablePayPeriod",
        "/positionBudget/expectedVariablePayCurrencyValue",
    ),
}

# Fields HiBob assigns or calculates itself. They are reported by the
# metadata endpoints and are useful in searches, but never belong on a form.
READ_ONLY_FIELDS: dict[str, frozenset[str]] = {
    OBJECT_TYPE_POSITION: frozenset(
        {
            "/position/id",
            "/position/status",
            "/position/filledBy",
            "/position/expectedStartDate",
            "/position/actualStartDate",
            "/position/endEffectiveDate",
            "/position/managerPositionFilledBy",
            "/position/recruitmentStatus",
            "/position/calculatedRecruitmentStatus",
            "/position/calculatedExpectedStartDate",
            "/position/calculatedActualStartDate",
            "/position/hasOpenRequests",
            "/position/modificationDate",
            "/position/budget",
            "/position/job",  # deprecated in favour of jobProfile
        }
    ),
    OBJECT_TYPE_OPENING: frozenset(
        {
            "/positionOpening/id",
            "/positionOpening/positionId",
            "/positionOpening/status",
            "/positionOpening/updateEffectiveDate",
            "/positionOpening/actualStartDate",
            "/positionOpening/filledBy",
        }
    ),
    OBJECT_TYPE_BUDGET: frozenset({"/positionBudget/id", "/positionBudget/positionId"}),
}

# Fixed vocabularies from HiBob's API reference. Used only when the metadata
# does not tie the field to a named list, since a company's own list is the
# authority whenever one exists.
DOCUMENTED_VALUES: dict[str, tuple[str, ...]] = {
    "/position/positionType": ("Growth", "Promotion", "Replacement"),
    "/position/employmentType": (
        "Permanent",
        "Temporary",
        "Apprentice",
        "Contractor",
        "Non-guaranteed",
    ),
    "/positionOpening/recruitmentStatus": ("open", "onHold", "closed"),
    "/positionBudget/salaryPayPeriod": (
        "Annual",
        "Annual 13",
        "Annual 14",
        "Hourly",
        "Daily",
        "Weekly",
        "Monthly",
        "Monthly 13",
        "Monthly 14",
        "Quarterly",
    ),
    "/positionBudget/variablePayPeriod": (
        "Quarterly",
        "Monthly",
        "Half-Yearly",
        "Annual",
    ),
}

# Options per list field before the form points at the full list instead;
# job catalogues in particular can run to thousands of items.
MAX_OPTIONS_PER_LIST = 100

# Guidance that applies to every form, phrased for the caller filling it in.
FORM_INSTRUCTIONS: tuple[str, ...] = (
    "Fill in every field with required=true; other fields may be omitted.",
    "For a field with 'options', submit the chosen option's 'id' (not its "
    "name). For a field with 'allowed_values', submit one of those strings "
    "exactly as written.",
    "'options' lists every valid item unless the field says "
    "'options_truncated'; then follow its 'options_note' to fetch the rest.",
    "Dates are ISO 8601 strings (YYYY-MM-DD). 'fte' is a percentage, so 100 "
    "means full time.",
    "Fields listed under 'read_only_fields' are set by HiBob and must not be sent.",
    "Submit each section's values as the argument named in its 'argument' "
    "key of the tool named in 'submit_with'.",
)


def normalize_metadata_fields(payload: Any) -> list[dict[str, Any]]:
    """Return the field descriptors from a metadata response.

    HiBob documents the response as a single field object while describing it
    as a list of fields, so a list, a single descriptor and a ``{"fields":
    [...]}`` wrapper are all accepted.
    """
    if isinstance(payload, dict):
        if isinstance(payload.get("fields"), list):
            payload = payload["fields"]
        elif "id" in payload:
            payload = [payload]
        else:
            return []
    if not isinstance(payload, list):
        return []
    return [field for field in payload if isinstance(field, dict)]


def field_id_of(object_type: str, descriptor: dict[str, Any]) -> str | None:
    """Pick the ``/objectType/name`` field ID out of a metadata descriptor.

    Prefers an ``id`` that already has that shape, then the JSON path root,
    and finally prefixes a bare name. Returns ``None`` if nothing usable is
    present.
    """
    raw_id = descriptor.get("id")
    json_path = descriptor.get("jsonPath")
    root = json_path.get("root") if isinstance(json_path, dict) else None
    for candidate in (raw_id, root):
        if isinstance(candidate, str) and candidate.startswith("/"):
            return candidate.rstrip("/")
    if isinstance(raw_id, str) and raw_id.strip():
        try:
            return normalize_field_key(object_type, raw_id)
        except ValueError:
            return raw_id.strip()
    return None


def index_named_lists(payload: Any) -> dict[str, list[Any]]:
    """Map list name to its items, whatever shape the named-lists call returns.

    The API reference documents an array of ``{"name": ..., "items": [...]}``;
    a mapping keyed by list name is accepted too.
    """
    index: dict[str, list[Any]] = {}
    if isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                items = entry.get("items")
                index[entry["name"]] = items if isinstance(items, list) else []
    elif isinstance(payload, dict):
        for name, entry in payload.items():
            if isinstance(entry, dict):
                items = entry.get("items")
                index[name] = items if isinstance(items, list) else []
            elif isinstance(entry, list):
                index[name] = entry
    return index


def lookup_named_list(index: dict[str, list[Any]], list_id: str) -> list[Any] | None:
    """Find a list by ID, tolerating a case difference in the name."""
    if list_id in index:
        return index[list_id]
    wanted = list_id.strip().lower()
    for name, items in index.items():
        if name.strip().lower() == wanted:
            return items
    return None


def count_list_items(items: Any) -> int:
    """Count every item in a list, nested children included."""
    if not isinstance(items, list):
        return 0
    total = 0
    for item in items:
        if isinstance(item, dict):
            total += 1 + count_list_items(item.get("children"))
    return total


def shape_list_items(items: Any, limit: int | None = None) -> list[dict[str, Any]]:
    """Reduce HiBob list items to what a drop-down needs.

    Keeps the ID (what gets submitted) and display name, adds ``value`` only
    when it differs from the name, ``archived`` only when true, and nested
    ``children`` for hierarchy lists. With ``limit``, at most that many items
    are kept, children included, so a huge list cannot swamp a form.
    """
    return _shape_list_items(items, [limit])


def _shape_list_items(items: Any, budget: list[int | None]) -> list[dict[str, Any]]:
    """Shape ``items``, spending one unit of ``budget`` (shared across the
    recursion) per item kept."""
    shaped: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return shaped
    for item in items:
        if not isinstance(item, dict):
            continue
        remaining = budget[0]
        if remaining is not None:
            if remaining <= 0:
                break
            budget[0] = remaining - 1
        name = item.get("name")
        value = item.get("value")
        option: dict[str, Any] = {
            "id": item.get("id"),
            "name": name if name is not None else value,
        }
        if value is not None and value != option["name"]:
            option["value"] = value
        if item.get("archived"):
            option["archived"] = True
        children = _shape_list_items(item.get("children"), budget)
        if children:
            option["children"] = children
        shaped.append(option)
    return shaped


def _humanize(field_id: str) -> str:
    """Turn ``/position/expectedStartDate`` into ``Expected start date``."""
    name = field_id.rsplit("/", 1)[-1]
    words: list[str] = []
    current = ""
    for char in name:
        if char.isupper() and current:
            words.append(current)
            current = char.lower()
        else:
            current += char
    if current:
        words.append(current)
    return " ".join(words).capitalize()


def build_form_section(
    object_type: str,
    metadata_payload: Any,
    named_lists: dict[str, list[Any]],
    *,
    role: str,
    argument: str,
) -> dict[str, Any]:
    """Build one object's section of a form.

    ``named_lists`` is the index from :func:`index_named_lists`. List IDs that
    are not in it are reported under ``unresolved_lists`` so the caller can
    fetch them individually or warn.
    """
    required = REQUIRED_FIELDS.get(object_type, frozenset())
    read_only = READ_ONLY_FIELDS.get(object_type, frozenset())
    documented = WRITABLE_FIELDS.get(object_type, ())

    fields: list[dict[str, Any]] = []
    read_only_fields: list[dict[str, Any]] = []
    unresolved: set[str] = set()
    seen: set[str] = set()

    for descriptor in normalize_metadata_fields(metadata_payload):
        field_id = field_id_of(object_type, descriptor)
        if not field_id or field_id in seen:
            continue
        seen.add(field_id)

        field_type = descriptor.get("fieldType")
        type_name = field_type.get("type") if isinstance(field_type, dict) else None
        type_data = field_type.get("typeData") if isinstance(field_type, dict) else None
        list_id = type_data.get("listId") if isinstance(type_data, dict) else None
        name = descriptor.get("name") or _humanize(field_id)

        if field_id in read_only:
            compact: dict[str, Any] = {"id": field_id, "name": name}
            if type_name:
                compact["type"] = type_name
            read_only_fields.append(compact)
            continue

        entry: dict[str, Any] = {"id": field_id, "name": name}
        description = descriptor.get("description")
        if description:
            entry["description"] = description
        if type_name:
            entry["type"] = type_name
        entry["required"] = field_id in required or bool(
            descriptor.get("required") or descriptor.get("mandatory")
        )
        if field_id not in documented:
            entry["documented"] = False
        if isinstance(list_id, str) and list_id:
            entry["list_id"] = list_id
            items = lookup_named_list(named_lists, list_id)
            if items is None:
                unresolved.add(list_id)
            else:
                total = count_list_items(items)
                entry["options"] = shape_list_items(items, limit=MAX_OPTIONS_PER_LIST)
                if total > MAX_OPTIONS_PER_LIST:
                    entry["options_truncated"] = True
                    entry["options_shown"] = MAX_OPTIONS_PER_LIST
                    entry["options_total"] = total
                    entry["options_note"] = (
                        f"Showing the first {MAX_OPTIONS_PER_LIST} of {total} "
                        "items. Call hibob_get_company_named_lists with "
                        f"list_name='{list_id}' for the full list before offering "
                        "a choice that is not shown here."
                    )
        if "options" not in entry and field_id in DOCUMENTED_VALUES:
            entry["allowed_values"] = list(DOCUMENTED_VALUES[field_id])
        fields.append(entry)

    # A documented field the metadata did not mention still belongs on the
    # form; a required one especially, or the form could never be submitted.
    for field_id in documented:
        if field_id in seen:
            continue
        seen.add(field_id)
        entry = {
            "id": field_id,
            "name": _humanize(field_id),
            "required": field_id in required,
            "note": (
                "Not reported by HiBob's metadata endpoint; taken from the "
                "HiBob API reference."
            ),
        }
        if field_id in DOCUMENTED_VALUES:
            entry["allowed_values"] = list(DOCUMENTED_VALUES[field_id])
        fields.append(entry)

    # Required fields first; otherwise keep HiBob's order (sort is stable).
    fields.sort(key=lambda entry: 0 if entry.get("required") else 1)

    section: dict[str, Any] = {
        "object_type": object_type,
        "role": role,
        "argument": argument,
        "required_fields": [f["id"] for f in fields if f.get("required")],
        "fields": fields,
    }
    if read_only_fields:
        section["read_only_fields"] = read_only_fields
    if unresolved:
        section["unresolved_lists"] = sorted(unresolved)
    return section


def collect_list_ids(*metadata_payloads: Any) -> set[str]:
    """Every named-list ID referenced by the given metadata responses."""
    list_ids: set[str] = set()
    for payload in metadata_payloads:
        for descriptor in normalize_metadata_fields(payload):
            field_type = descriptor.get("fieldType")
            if not isinstance(field_type, dict):
                continue
            type_data = field_type.get("typeData")
            list_id = type_data.get("listId") if isinstance(type_data, dict) else None
            if isinstance(list_id, str) and list_id:
                list_ids.add(list_id)
    return list_ids
