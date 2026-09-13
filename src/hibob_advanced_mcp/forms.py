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

import re
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
    # No positionId: a budget is linked to its position by "/position/budget"
    # on the position, not by a field of its own.
    OBJECT_TYPE_BUDGET: frozenset({"/positionBudget/id"}),
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
# job catalogues in particular can run to thousands of items. Slack's static
# select allows 100, hence the default.
MAX_OPTIONS_PER_LIST = 100

# Position fields whose lists are too large to inline as they are, with the
# form-tool argument that narrows each one. Job profiles and manager positions
# are trees (job family > profile; department > job > position) and only the
# leaves are submittable, so their options are the leaves, labelled by path.
DEPARTMENT_FIELD = "/position/department"
JOB_PROFILE_FIELD = "/position/jobProfile"
MANAGER_POSITION_FIELD = "/position/managerPositionId"
NARROWING_ARGUMENTS: dict[str, str] = {
    DEPARTMENT_FIELD: "department",
    JOB_PROFILE_FIELD: "job_profile",
    MANAGER_POSITION_FIELD: "manager",
}
PATH_SEPARATOR = " > "
# Words a query can contain that never help pick a leaf.
QUERY_FILLER_WORDS = frozenset(
    {"a", "an", "and", "at", "for", "in", "of", "on", "the", "to", "with"}
)

# The tool that turns option names back into IDs, named on every list field
# so a caller left with only the names can find its way back.
RESOLVE_LIST_VALUES_TOOL = "hibob_resolve_list_values"

# Guidance that applies to every form, phrased for the caller filling it in.
FORM_INSTRUCTIONS: tuple[str, ...] = (
    "Fill in every field with required=true; other fields may be omitted.",
    "For a field with 'options', submit the chosen option's 'id' (not its "
    "name). For a field with 'allowed_values', submit one of those strings "
    "exactly as written.",
    "'options' lists every valid item unless the field says "
    "'options_truncated'; then follow its 'options_note' to fetch the rest.",
    "If only the chosen option names survive (after a form round trip, say), "
    "call hibob_resolve_list_values with the field and the names to get the "
    "IDs back; each list field names it as 'resolve_with'.",
    "Dates are ISO 8601 strings (YYYY-MM-DD). 'fte' is a percentage, so 100 "
    "means full time.",
    "Fields listed under 'read_only_fields' are set by HiBob and must not be sent.",
    "A field with 'value' is pre-filled from what the user already said; keep "
    "it unless they change their mind.",
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
    max_options: int = MAX_OPTIONS_PER_LIST,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one object's section of a form.

    ``named_lists`` is the index from :func:`index_named_lists`. List IDs that
    are not in it are reported under ``unresolved_lists`` so the caller can
    fetch them individually or warn. ``overrides`` (from
    :func:`narrow_position_lists`) can replace a field's ``options`` and
    pre-fill its ``value``.
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
        override = (overrides or {}).get(field_id) or {}
        if isinstance(list_id, str) and list_id:
            entry["list_id"] = list_id
            entry["resolve_with"] = RESOLVE_LIST_VALUES_TOOL
            items = lookup_named_list(named_lists, list_id)
            if "options" in override:
                entry["options"] = list(override["options"])
            elif items is None:
                unresolved.add(list_id)
            else:
                total = count_list_items(items)
                entry["options"] = shape_list_items(items, limit=max_options)
                if total > max_options:
                    entry["options_truncated"] = True
                    entry["options_shown"] = max_options
                    entry["options_total"] = total
                    entry["options_note"] = (
                        f"Showing the first {max_options} of {total} "
                        "items. Call hibob_get_company_named_lists with "
                        f"list_name='{list_id}' for the full list before offering "
                        "a choice that is not shown here."
                    )
        if "options" not in entry and field_id in DOCUMENTED_VALUES:
            entry["allowed_values"] = list(DOCUMENTED_VALUES[field_id])
        for key in ("value", "value_name"):
            if key in override:
                entry[key] = override[key]
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


def field_list_ids(object_type: str, metadata_payload: Any) -> dict[str, str]:
    """Map each list-backed field ID to the named list it draws from."""
    mapping: dict[str, str] = {}
    for descriptor in normalize_metadata_fields(metadata_payload):
        field_id = field_id_of(object_type, descriptor)
        field_type = descriptor.get("fieldType")
        type_data = field_type.get("typeData") if isinstance(field_type, dict) else None
        list_id = type_data.get("listId") if isinstance(type_data, dict) else None
        if field_id and isinstance(list_id, str) and list_id:
            mapping[field_id] = list_id
    return mapping


# ---------------------------------------------------------------------------
# Narrowing large lists from what the user has already said
# ---------------------------------------------------------------------------


def tokenize(text: Any) -> list[str]:
    """Lower-case words of ``text``, split on anything that is not a letter or digit."""
    return [word for word in re.split(r"[^0-9a-z]+", str(text or "").lower()) if word]


def flatten_leaves(items: Any, prefix: str = "") -> list[dict[str, Any]]:
    """The leaves of a list tree, each labelled with its path.

    Only leaves can be submitted for a tree-shaped list, so a form offers
    them directly, named by their path ("Data > Head of Data > P-1 · London ·
    Jane Doe") so that any level of the path can be matched.
    """
    leaves: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return leaves
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if name is None:
            name = item.get("value")
        label = f"{prefix}{PATH_SEPARATOR}{name}" if prefix else str(name)
        children = item.get("children")
        if isinstance(children, list) and children:
            leaves.extend(flatten_leaves(children, label))
            continue
        leaf: dict[str, Any] = {"id": item.get("id"), "name": label}
        if item.get("archived"):
            leaf["archived"] = True
        leaves.append(leaf)
    return leaves


def _word_hits(words: list[str], label: Any) -> int:
    label_words = tokenize(label)
    return sum(
        1
        for word in words
        if any(label_word.startswith(word) for label_word in label_words)
    )


def rank_matches(
    leaves: list[dict[str, Any]], query: Any, *, require_all: bool = False
) -> list[dict[str, Any]]:
    """The leaves that ``query`` picks out, best first.

    An ID or an exact name wins outright. Otherwise every word of the query
    (filler words such as "in" and "of" aside) must start a word of the
    leaf's label, in any order; failing that, leaves sharing any word are
    returned, those sharing more first, unless ``require_all`` is set, in
    which case nothing is.
    """
    text = str(query or "").strip()
    if not text:
        return []
    by_id = [leaf for leaf in leaves if str(leaf.get("id")) == text]
    if by_id:
        return by_id
    lowered = text.lower()
    exact = [
        leaf for leaf in leaves if str(leaf.get("name", "")).strip().lower() == lowered
    ]
    if exact:
        return exact
    words = [word for word in tokenize(text) if word not in QUERY_FILLER_WORDS]
    if not words:
        return []
    scored = [
        (hits, leaf) for leaf in leaves if (hits := _word_hits(words, leaf.get("name")))
    ]
    full = [leaf for hits, leaf in scored if hits == len(words)]
    if full or require_all:
        return full
    scored.sort(key=lambda pair: -pair[0])
    return [leaf for _, leaf in scored]


def subtree_for(tree: Any, name: Any) -> list[Any] | None:
    """Children of the top-level node called ``name``, or None if there is none."""
    wanted = str(name or "").strip().lower()
    if not wanted or not isinstance(tree, list):
        return None
    for item in tree:
        if (
            isinstance(item, dict)
            and str(item.get("name", "")).strip().lower() == wanted
        ):
            children = item.get("children")
            return children if isinstance(children, list) else []
    return None


def _titles(leaves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The distinct first path segments of ``leaves``, as name-only candidates."""
    seen: list[str] = []
    for leaf in leaves:
        title = str(leaf.get("name", "")).split(PATH_SEPARATOR, 1)[0]
        if title not in seen:
            seen.append(title)
    return [{"name": title} for title in seen]


def _question(
    field_id: str,
    ask: str,
    *,
    hint: str | None,
    matched: int,
    candidates: list[dict[str, Any]],
    max_options: int,
) -> dict[str, Any]:
    question: dict[str, Any] = {
        "field": field_id,
        "argument": NARROWING_ARGUMENTS[field_id],
        "ask": ask,
    }
    if hint is not None:
        question["hint"] = hint
        question["matched"] = matched
    if candidates and len(candidates) <= max_options:
        question["candidates"] = candidates
    return question


def _choice(matches: list[dict[str, Any]]) -> dict[str, Any]:
    override: dict[str, Any] = {"options": matches}
    if len(matches) == 1:
        override["value"] = matches[0]["id"]
        override["value_name"] = matches[0]["name"]
    return override


def narrow_position_lists(
    metadata_payload: Any,
    named_lists: dict[str, list[Any]],
    *,
    department: str | None,
    job_profile: str | None,
    manager: str | None,
    max_options: int,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Narrow the position form's large lists from what the user has said.

    Returns per-field overrides for :func:`build_form_section` and the
    questions still to ask before every one of these fields can be offered
    within ``max_options``. A department narrows the manager choices to that
    department's branch of the position tree and the job profiles to those
    whose label names the department; a job title and a manager's name pick
    out leaves directly.
    """
    lists = field_list_ids(OBJECT_TYPE_POSITION, metadata_payload)
    overrides: dict[str, dict[str, Any]] = {}
    questions: list[dict[str, Any]] = []

    def items_for(field_id: str) -> list[Any] | None:
        list_id = lists.get(field_id)
        return lookup_named_list(named_lists, list_id) if list_id else None

    # Department: a single match pre-fills the field and scopes the others.
    chosen_department: dict[str, Any] | None = None
    department_items = items_for(DEPARTMENT_FIELD)
    departments = (
        flatten_leaves(department_items) if department_items is not None else []
    )
    if department and departments:
        matches = rank_matches(departments, department)
        if len(matches) == 1:
            chosen_department = matches[0]
            overrides[DEPARTMENT_FIELD] = {
                "value": chosen_department["id"],
                "value_name": chosen_department["name"],
            }
        else:
            ask = (
                f"No department matches {department!r}. Which department is this "
                "position in?"
                if not matches
                else f"{len(matches)} departments match {department!r}. Which one "
                "is it?"
            )
            questions.append(
                _question(
                    DEPARTMENT_FIELD,
                    ask,
                    hint=department,
                    matched=len(matches),
                    candidates=matches or departments,
                    max_options=max_options,
                )
            )
    elif len(departments) > max_options:
        questions.append(
            _question(
                DEPARTMENT_FIELD,
                "Which department is this position in?",
                hint=None,
                matched=0,
                candidates=[],
                max_options=max_options,
            )
        )

    # Manager: matched by name anywhere in the tree, else the department branch.
    position_items = items_for(MANAGER_POSITION_FIELD)
    if position_items is not None:
        every_position = flatten_leaves(position_items)
        branch = (
            subtree_for(position_items, chosen_department["name"])
            if chosen_department
            else None
        )
        scoped = flatten_leaves(branch) if branch is not None else every_position
        if manager:
            matches = rank_matches(every_position, manager)
            if 0 < len(matches) <= max_options:
                overrides[MANAGER_POSITION_FIELD] = _choice(matches)
            else:
                ask = (
                    f"No position matches {manager!r} as the manager. Who does this "
                    "position report to? Give the manager's name or their position."
                    if not matches
                    else f"{len(matches)} positions match {manager!r}. Which one is "
                    "the manager?"
                )
                questions.append(
                    _question(
                        MANAGER_POSITION_FIELD,
                        ask,
                        hint=manager,
                        matched=len(matches),
                        candidates=matches or scoped,
                        max_options=max_options,
                    )
                )
        elif len(scoped) <= max_options:
            overrides[MANAGER_POSITION_FIELD] = {"options": scoped}
        else:
            questions.append(
                _question(
                    MANAGER_POSITION_FIELD,
                    "Who does this position report to? Give the manager's name or "
                    "their position.",
                    hint=None,
                    matched=0,
                    candidates=scoped,
                    max_options=max_options,
                )
            )

    # Job profile: scoped to profiles naming the department, then by title.
    profile_items = items_for(JOB_PROFILE_FIELD)
    if profile_items is not None:
        every_profile = flatten_leaves(profile_items)
        scoped = every_profile
        if chosen_department:
            words = tokenize(chosen_department["name"])
            within = [
                leaf
                for leaf in every_profile
                if words and _word_hits(words, leaf.get("name")) == len(words)
            ]
            if within:
                scoped = within
        if job_profile:
            matches = rank_matches(scoped, job_profile) or rank_matches(
                every_profile, job_profile
            )
            if 0 < len(matches) <= max_options:
                overrides[JOB_PROFILE_FIELD] = _choice(matches)
            else:
                ask = (
                    f"No job profile matches {job_profile!r}. What is the job title?"
                    if not matches
                    else f"{len(matches)} job profiles match {job_profile!r}. Which "
                    "title fits best?"
                )
                questions.append(
                    _question(
                        JOB_PROFILE_FIELD,
                        ask,
                        hint=job_profile,
                        matched=len(matches),
                        candidates=matches or _titles(scoped),
                        max_options=max_options,
                    )
                )
        elif len(scoped) <= max_options:
            overrides[JOB_PROFILE_FIELD] = {"options": scoped}
        else:
            questions.append(
                _question(
                    JOB_PROFILE_FIELD,
                    "What kind of role is this? Give a job title.",
                    hint=None,
                    matched=0,
                    candidates=_titles(scoped),
                    max_options=max_options,
                )
            )

    return overrides, questions
