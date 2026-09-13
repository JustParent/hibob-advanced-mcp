"""HiBob Workforce Planning tools: positions, position openings and budgets.

Tools are registered through :func:`register_workforce_planning_tools` so that
write tools can be withheld in read-only deployments, and so further HiBob
domains can be added as sibling modules.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from .cache import NamedListCache
from .client import HiBobClient, get_client
from .costs import (
    BUDGET_ID_FIELD,
    GROUP_BY_FIELDS,
    POSITION_BUDGET_REF_FIELD,
    attach_positions,
    cell_value,
    index_positions_by_budget,
    join_positions_to_budgets,
    summarize_costs,
)
from .envelopes import (
    NESTED_POSITION_BUDGET_KEY,
    NESTED_POSITION_OPENING_KEY,
    OBJECT_TYPE_BUDGET,
    OBJECT_TYPE_OPENING,
    OBJECT_TYPE_POSITION,
    build_items_envelope,
    flatten_search_entries,
    normalize_field_key,
    normalize_id,
    validate_required_keys,
)
from .errors import HiBobApiError, format_exception
from .forms import (
    FORM_INSTRUCTIONS,
    MAX_OPTIONS_PER_LIST,
    READ_ONLY_FIELDS,
    REQUIRED_FIELDS,
    build_form_section,
    collect_list_ids,
    count_list_items,
    index_named_lists,
    narrow_position_lists,
    rank_matches,
    shape_list_items,
)
from .hierarchy import (
    HIERARCHY_FIELDS,
    RESOLVED_BY_EMAIL,
    counts_by_status,
    positions_under,
    resolve_root,
    shape_position,
    summarize_tree,
)
from .references import (
    OPENING_NAME_FIELD,
    OPENING_REF_FIELDS,
    POSITION_NAME_FIELD,
    POSITION_REF_FIELDS,
    OpeningRef,
    PositionRef,
    budget_to_write,
    check_opening_parent,
    is_numeric_id,
    reference_filter,
    refuse_existing_budget,
    single_match,
    split_references,
)

# Endpoint paths, relative to the versioned API base.
POSITION_METADATA_PATH = "/metadata/objects/position"
OPENING_METADATA_PATH = "/positions/position-openings/metadata"
BUDGET_METADATA_PATH = "/positions/position-budget/metadata"
NAMED_LISTS_PATH = "/company/named-lists"

POSITION_SEARCH_PATH = "/objects/position/search"
OPENING_SEARCH_PATH = "/positions/position-openings/search"
BUDGET_SEARCH_PATH = "/positions/position-budget/search"

POSITIONS_PATH = "/workforce-planning/positions"

# The server's only use of HiBob's people API: one filtered search asking for
# an employee ID by email, so a person named by email can be matched to the
# position they hold. No other employee data is ever requested.
PEOPLE_SEARCH_PATH = "/people/search"

METADATA_PATHS = {
    OBJECT_TYPE_POSITION: POSITION_METADATA_PATH,
    OBJECT_TYPE_OPENING: OPENING_METADATA_PATH,
    OBJECT_TYPE_BUDGET: BUDGET_METADATA_PATH,
}

REQUIRED_POSITION_FIELDS = set(REQUIRED_FIELDS[OBJECT_TYPE_POSITION])
REQUIRED_OPENING_FIELDS = set(REQUIRED_FIELDS[OBJECT_TYPE_OPENING])
REQUIRED_BUDGET_FIELDS = set(REQUIRED_FIELDS[OBJECT_TYPE_BUDGET])

UPDATABLE_POSITION_FIELDS = {
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
}

ObjectTypeLiteral = Literal["position", "positionOpening", "positionBudget"]
# The seat's status: HiBob's positionOpeningStatuses list, which goes beyond
# the four values its API reference names.
OpeningStatusLiteral = Literal[
    "vacant",
    "starting",
    "filled",
    "departing",
    "cancelled",
    "onHold",
    "cancelledSoon",
]
OPENING_STATUS_VALUES = (
    "vacant, starting, filled, departing, cancelled, onHold, cancelledSoon"
)
# HiBob's positionStatus list. It carries the same seven values as
# positionOpeningStatuses, but it is a different list, so it is named apart.
PositionStatusLiteral = Literal[
    "vacant",
    "starting",
    "filled",
    "departing",
    "cancelled",
    "onHold",
    "cancelledSoon",
]
POSITION_STATUS_VALUES = (
    "vacant, starting, filled, departing, cancelled, onHold, cancelledSoon"
)

OPENING_ID_FIELD = "/positionOpening/id"
OPENING_POSITION_ID_FIELD = "/positionOpening/positionId"
OPENING_STATUS_FIELD = "/positionOpening/status"

# What hibob_get_openings_for_positions returns when no fields are requested.
DEFAULT_OPENING_FIELDS = (
    OPENING_ID_FIELD,
    OPENING_POSITION_ID_FIELD,
    "/positionOpening/positionOpeningName",
    "/positionOpening/status",
    "/positionOpening/recruitmentStatus",
    "/positionOpening/expectedStartDate",
    "/positionOpening/actualStartDate",
    "/positionOpening/filledBy",
)

# HiBob's opening search cannot filter by position, so openings are fetched
# in full and joined on positionId here. Rather than rely on HiBob accepting
# an empty filter list, the scan sends a clause every opening satisfies. This
# is the value verified against HiBob's sandbox; an opening whose ID happened
# to equal it would be left out.
MATCH_ALL_SENTINEL_OPENING_ID = "1"
OPENING_SCAN_PAGE_SIZE = 100
# Bounds a scan at 10,000 openings, so a paging fault cannot loop forever.
MAX_OPENING_SCAN_PAGES = 100

# Fields read back after a write, besides the ones that were written, so the
# caller sees the record as HiBob now holds it.
VERIFY_POSITION_FIELDS = (
    "/position/id",
    "/position/name",
    "/position/status",
    "/position/recruitmentStatus",
    "/position/department",
    "/position/site",
    "/position/jobProfile",
    "/position/fte",
    "/position/effectiveDate",
    "/position/managerPositionId",
    "/position/expectedStartDate",
)
VERIFY_OPENING_FIELDS = DEFAULT_OPENING_FIELDS
# A budget carries no positionId: the only link is "/position/budget" on the
# position itself. HiBob drops unrecognized field IDs silently, so a phantom
# field here would cost the caller a real one rather than raising.
VERIFY_BUDGET_FIELDS = (
    "/positionBudget/id",
    "/positionBudget/currency",
    "/positionBudget/salaryPayPeriod",
    "/positionBudget/expectedBaseSalaryCurrencyValue",
    "/positionBudget/totalPositionCostCurrencyValue",
    "/positionBudget/convertedTotalCostCurrencyValue",
    "/positionBudget/proRatedCostCurrencyValue",
    "/positionBudget/proRatedCostPercentage",
    "/positionBudget/variablePayPeriod",
    "/positionBudget/expectedVariablePayCurrencyValue",
)
MAX_SEARCH_FIELDS = 50

# Cost lives on the positionBudget object, reachable only through
# "/position/budget" on the position. These are the figures the HiBob UI shows
# on a position's cost panel.
DEFAULT_BUDGET_COST_FIELDS = (
    BUDGET_ID_FIELD,
    "/positionBudget/currency",
    "/positionBudget/salaryPayPeriod",
    "/positionBudget/expectedBaseSalaryCurrencyValue",
    "/positionBudget/totalPositionCostCurrencyValue",
    "/positionBudget/convertedTotalCostCurrencyValue",
    "/positionBudget/proRatedCostCurrencyValue",
    "/positionBudget/proRatedCostPercentage",
    "/positionBudget/expectedVariablePayCurrencyValue",
    "/positionBudget/variablePayPeriod",
)
# What each costed row says about the position itself.
DEFAULT_POSITION_COST_FIELDS = (
    "/position/id",
    "/position/name",
    "/position/position",
    "/position/status",
    "/position/department",
    "/position/site",
    "/position/jobProfile",
    "/position/effectiveDate",
)
# One page holds every budget in a company of this size; the cursor is still
# followed, so a larger tenant is not silently truncated.
BUDGET_PAGE_SIZE = 1000
MAX_BUDGET_SCAN_PAGES = 100
GROUP_BY_VALUES = ", ".join(sorted(GROUP_BY_FIELDS))
GroupByLiteral = Literal["department", "site", "status", "jobProfile", "currency"]

# What a free-text position query is matched against, since HiBob cannot
# filter on any of them: the title, code, department, site, job profile and
# holder. Their labels are joined into one line per position.
POSITION_QUERY_FIELDS = (
    "/position/position",
    "/position/name",
    "/position/department",
    "/position/site",
    "/position/jobProfile",
    "/position/filledBy",
)
QUERY_LABEL_SEPARATOR = " · "

# Sections of each form: (object type, role, argument of the submit tool).
FORM_LAYOUTS: dict[str, tuple[tuple[str, str, str], ...]] = {
    OBJECT_TYPE_POSITION: (
        (OBJECT_TYPE_POSITION, "primary", "position_fields"),
        (OBJECT_TYPE_OPENING, "nested_required", "opening_fields"),
        (OBJECT_TYPE_BUDGET, "nested_optional", "budget_fields"),
    ),
    OBJECT_TYPE_OPENING: ((OBJECT_TYPE_OPENING, "primary", "fields"),),
    OBJECT_TYPE_BUDGET: ((OBJECT_TYPE_BUDGET, "primary", "fields"),),
}
FORM_SUBMIT_TOOLS = {
    OBJECT_TYPE_POSITION: "hibob_create_position",
    OBJECT_TYPE_OPENING: "hibob_create_position_opening",
    OBJECT_TYPE_BUDGET: "hibob_create_position_budget",
}


class SearchFilter(BaseModel):
    """One filter clause in a workforce planning search."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    field_id: str = Field(
        ...,
        description=(
            "Field to filter on, e.g. '/position/status' or '/positionOpening/status'."
        ),
        min_length=1,
    )
    operator: Literal["equals", "notEqual"] = Field(
        default="equals", description="Comparison operator."
    )
    values: list[str] = Field(
        ...,
        description="Values to compare against; the filter matches any of them.",
        min_length=1,
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "fieldId": self.field_id,
            "operator": self.operator,
            "values": list(self.values),
        }


MATCH_ALL_OPENINGS_FILTER = SearchFilter(
    field_id=OPENING_ID_FIELD,
    operator="notEqual",
    values=[MATCH_ALL_SENTINEL_OPENING_ID],
)
# The same sentinel serves the other searches: HiBob accepts no empty filter
# list on any of them, so a search without filters sends one of these.
MATCH_ALL_POSITIONS_FILTER = SearchFilter(
    field_id="/position/id",
    operator="notEqual",
    values=[MATCH_ALL_SENTINEL_OPENING_ID],
)
MATCH_ALL_BUDGETS_FILTER = SearchFilter(
    field_id="/positionBudget/id",
    operator="notEqual",
    values=[MATCH_ALL_SENTINEL_OPENING_ID],
)


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


def _openings_for_positions(
    entries: list[dict[str, Any]], position_ids: list[str]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Keep the openings whose positionId is one of ``position_ids``.

    Returns the matching entries and a count per requested position, so a
    position with no openings is reported as 0 rather than going missing.
    """
    counts = {position_id: 0 for position_id in position_ids}
    matched: list[dict[str, Any]] = []
    for entry in entries:
        values = entry.get("values")
        if not isinstance(values, dict):
            continue
        key = normalize_id(values.get(OPENING_POSITION_ID_FIELD))
        if key in counts:
            counts[key] += 1
            matched.append(entry)
    return matched, counts


async def _scan_openings(
    client: HiBobClient, body: dict[str, Any]
) -> tuple[list[dict[str, Any]], bool]:
    """Page through every opening that ``body``'s filters match.

    Returns the flattened entries and whether the last page was reached. A
    repeated cursor or the page cap ends the scan early rather than looping.
    """
    entries: list[dict[str, Any]] = []
    seen_cursors: set[str] = set()
    cursor: str | None = None
    for _ in range(MAX_OPENING_SCAN_PAGES):
        pagination: dict[str, Any] = {"limit": OPENING_SCAN_PAGE_SIZE}
        if cursor:
            pagination["cursor"] = cursor
        payload = await client.search(
            OPENING_SEARCH_PATH, {**body, "pagination": pagination}
        )
        page = _paged_search_result(payload, "positionOpeningEntries")
        entries.extend(page["entries"])
        cursor = page.get("next_cursor")
        if not cursor:
            return entries, True
        if cursor in seen_cursors:
            return entries, False
        seen_cursors.add(cursor)
    return entries, False


def _named_list_path(list_name: str, include_archived: bool) -> str:
    path = f"{NAMED_LISTS_PATH}/{list_name.strip()}"
    return f"{path}?includeArchived=true" if include_archived else path


def _named_list_items(payload: Any) -> list[Any]:
    """Items of a single-list response.

    The live endpoint answers ``{"name", "values", "items"}`` with the items
    repeated under both keys; the reference documents ``items`` alone. A
    bare list is accepted too.
    """
    if isinstance(payload, dict):
        items = payload.get("items")
        if items is None:
            items = payload.get("values")
        return items if isinstance(items, list) else []
    return payload if isinstance(payload, list) else []


# Cache key for the summary of every list, which no real list ID can collide with.
ALL_LISTS_CACHE_ID = "*"


async def _fetch_named_list(
    client: HiBobClient, list_id: str, include_archived: bool, cache: NamedListCache
) -> list[Any]:
    key = (list_id, include_archived)
    cached = cache.get(key)
    if cached is not None:
        return cached
    items = _named_list_items(
        await client.get(_named_list_path(list_id, include_archived))
    )
    cache.set(key, items)
    return items


async def _summarize_named_lists(
    client: HiBobClient, include_archived: bool, cache: NamedListCache
) -> list[dict[str, Any]]:
    """Name and size of every list, from the combined endpoint."""
    key = (ALL_LISTS_CACHE_ID, include_archived)
    cached = cache.get(key)
    if cached is not None:
        return cached
    suffix = "?includeArchived=true" if include_archived else ""
    index = index_named_lists(await client.get(f"{NAMED_LISTS_PATH}{suffix}"))
    summary = [
        {"name": name, "items": count_list_items(items)}
        for name, items in index.items()
    ]
    cache.set(key, summary)
    return summary


async def _resolve_named_lists(
    client: HiBobClient,
    list_ids: set[str],
    include_archived: bool,
    cache: NamedListCache,
) -> tuple[dict[str, list[Any]], list[str]]:
    """Fetch the named lists behind ``list_ids``, as an index by list name.

    Each list is fetched on its own, in parallel, unless the cache still holds
    it. The combined named-lists endpoint is never used: it returns every list
    in the company, which can run to tens of megabytes. Failures become
    warnings rather than errors, since a form without drop-down options is
    still worth returning.
    """
    ordered = sorted(list_ids)
    results = await asyncio.gather(
        *(
            _fetch_named_list(client, list_id, include_archived, cache)
            for list_id in ordered
        ),
        return_exceptions=True,
    )
    index: dict[str, list[Any]] = {}
    warnings: list[str] = []
    for list_id, result in zip(ordered, results, strict=True):
        if isinstance(result, HiBobApiError):
            warnings.append(
                f"List {list_id!r} could not be fetched, so its field has no "
                f"options: {result}"
            )
        elif isinstance(result, BaseException):
            raise result
        else:
            index[list_id] = result
    return index, warnings


async def _employee_ids_for_email(client: HiBobClient, email: str) -> list[str]:
    """IDs of the employees whose work email is ``email``, and nothing else.

    Deliberately narrow: one email in, employee IDs out. It exists so that a
    person named by email can be matched to the position they hold, not to
    read employee data.
    """
    body = {
        "fields": ["root.id"],
        "filters": [
            {"fieldPath": "root.email", "operator": "equals", "values": [email]}
        ],
    }
    try:
        payload = await client.search(PEOPLE_SEARCH_PATH, body)
    except HiBobApiError as exc:
        if exc.status_code == 403:
            raise HiBobApiError(
                "HiBob denied the employee lookup by email (403). The service "
                "user needs permission to read employees' email addresses; "
                "otherwise give the person's name or employee ID instead.",
                status_code=403,
                hibob_key=exc.hibob_key,
                hibob_error=exc.hibob_error,
            ) from exc
        raise
    employees = payload.get("employees") if isinstance(payload, dict) else None
    return [
        normalize_id(employee["id"])
        for employee in employees or []
        if isinstance(employee, dict) and employee.get("id") is not None
    ]


def _read_back_fields(
    object_type: str, base: tuple[str, ...], written: dict[str, Any] | None
) -> list[str]:
    """The fields to read back after a write: the basics plus what was written."""
    fields = list(base)
    for key in written or {}:
        try:
            field_id = normalize_field_key(object_type, key)
        except ValueError:
            continue
        if field_id in (NESTED_POSITION_OPENING_KEY, NESTED_POSITION_BUDGET_KEY):
            continue
        if field_id not in fields:
            fields.append(field_id)
    return fields[:MAX_SEARCH_FIELDS]


async def _read_back(
    client: HiBobClient,
    path: str,
    id_field: str,
    record_id: Any,
    fields: list[str],
    *,
    paginated: bool,
) -> dict[str, Any] | None:
    """Fetch one record by ID through its search endpoint."""
    body = _search_body(
        fields,
        [SearchFilter(field_id=id_field, values=[normalize_id(record_id)])],
        True,
    )
    if paginated:
        body["pagination"] = {"limit": 1}
    payload = await client.search(path, body)
    entries = _paged_search_result(payload, "values")["entries"]
    return entries[0] if entries else None


async def _verify(
    label: str, coro: Awaitable[dict[str, Any] | None]
) -> tuple[dict[str, Any] | None, str | None]:
    """Run a read-back, reporting failure rather than raising.

    The write has already happened; a failure to read it back must not be
    mistaken for a failed write.
    """
    try:
        record = await coro
    except Exception as exc:
        return None, f"{label}: {format_exception(exc).removeprefix('Error: ')}"
    if record is None:
        return None, f"{label} was not found when read back"
    return record, None


POSITION_REF_DESCRIPTION = (
    "The position, as its numeric ID ('/position/id') or the name HiBob "
    "shows, such as 'P-0000000368'."
)
OPENING_REF_DESCRIPTION = (
    "The opening, as its numeric ID ('/positionOpening/id') or the name HiBob "
    "shows, such as 'O-6853240227'."
)
POSITION_REFS_DESCRIPTION = (
    "One or more positions, each as its numeric ID ('/position/id') or the "
    "name HiBob shows, such as 'P-0000000368'."
)


async def _resolve_position(
    client: HiBobClient, value: Any, *, lookup_ids: bool
) -> PositionRef:
    """The position a caller means, by numeric ID or by name.

    A name costs one search. An ID is taken as given unless ``lookup_ids``
    is set, which the budget tools need: the budget a position carries is
    only known from the position itself.
    """
    if is_numeric_id(value) and not lookup_ids:
        return PositionRef(id=normalize_id(value), name=None, budget_id=None)
    clause = reference_filter(
        value, id_field="/position/id", name_field=POSITION_NAME_FIELD
    )
    payload = await client.search(
        POSITION_SEARCH_PATH,
        {
            "fields": list(POSITION_REF_FIELDS),
            "filters": [clause],
            "includeHumanReadable": False,
        },
    )
    rows = _raw_rows(payload, "positionEntries")
    row = single_match(rows, "position", clause["values"][0], id_field="/position/id")
    return PositionRef.from_row(row)


async def _resolve_opening(client: HiBobClient, value: Any) -> OpeningRef:
    """The opening a caller means, by numeric ID or by name, with its parent.

    Always one search: the parent position it returns is what lets a write
    under the wrong position be refused before it is sent.
    """
    clause = reference_filter(
        value, id_field=OPENING_ID_FIELD, name_field=OPENING_NAME_FIELD
    )
    payload = await client.search(
        OPENING_SEARCH_PATH,
        {
            "fields": list(OPENING_REF_FIELDS),
            "filters": [clause],
            "includeHumanReadable": False,
            "pagination": {"limit": 2},
        },
    )
    rows = _raw_rows(payload, "positionOpeningEntries")
    row = single_match(rows, "opening", clause["values"][0], id_field=OPENING_ID_FIELD)
    return OpeningRef.from_row(row)


async def _resolve_position_ids(
    client: HiBobClient, values: list[Any]
) -> tuple[list[str], dict[str, str]]:
    """Numeric IDs for a list of position references, in the order given.

    Names are resolved together in one search. Returns the IDs and, for each
    name that was given, the ID it resolved to.
    """
    ids, names = split_references(values)
    if not names:
        return ids, {}
    payload = await client.search(
        POSITION_SEARCH_PATH,
        {
            "fields": list(POSITION_REF_FIELDS),
            "filters": [
                {"fieldId": POSITION_NAME_FIELD, "operator": "equals", "values": names}
            ],
            "includeHumanReadable": False,
        },
    )
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in _raw_rows(payload, "positionEntries"):
        name = str(cell_value(row, POSITION_NAME_FIELD) or "").strip().lower()
        by_name.setdefault(name, []).append(row)
    resolved: dict[str, str] = {}
    for name in names:
        row = single_match(
            by_name.get(name.lower(), []), "position", name, id_field="/position/id"
        )
        resolved[name] = PositionRef.from_row(row).id
    ordered: list[str] = []
    for raw in values:
        text = normalize_id(raw)
        position_id = text if text.isdigit() else resolved[text]
        if position_id not in ordered:
            ordered.append(position_id)
    return ordered, resolved


def _finish_write(result: dict[str, Any], problems: list[str]) -> dict[str, Any]:
    result["verified"] = not problems
    if problems:
        result["verification_error"] = "; ".join(problems)
    return result


def _unwrap(value: Any) -> Any:
    """Strip HiBob's ``{"value": ...}`` wrappers, including money values."""
    while isinstance(value, dict) and "value" in value:
        value = value["value"]
    return value


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _same_value(sent: Any, got: Any) -> bool:
    """Whether a value read back matches what was written, allowing for the
    shapes HiBob uses: numbers as strings, lists in any order, labels in
    another case, money wrapped with its currency."""
    sent, got = _unwrap(sent), _unwrap(got)
    if isinstance(sent, bool) or isinstance(got, bool):
        return bool(sent) == bool(got)
    if isinstance(sent, (list, tuple)) or isinstance(got, (list, tuple)):
        return sorted(normalize_id(x).lower() for x in _as_list(sent)) == sorted(
            normalize_id(x).lower() for x in _as_list(got)
        )
    if sent is None or got is None:
        return sent is None and got is None
    return normalize_id(sent).lower() == normalize_id(got).lower()


def _unconfirmed_fields(
    object_type: str, written: dict[str, Any] | None, record: dict[str, Any] | None
) -> dict[str, dict[str, Any]]:
    """The written fields whose read-back value differs from what was sent."""
    values = record.get("values") if isinstance(record, dict) else None
    values = values if isinstance(values, dict) else {}
    unconfirmed: dict[str, dict[str, Any]] = {}
    for key, sent in (written or {}).items():
        try:
            field_id = normalize_field_key(object_type, key)
        except ValueError:
            continue
        if field_id in (NESTED_POSITION_OPENING_KEY, NESTED_POSITION_BUDGET_KEY):
            continue
        got = values.get(field_id)
        if not _same_value(sent, got):
            unconfirmed[field_id] = {"sent": _unwrap(sent), "read_back": _unwrap(got)}
    return unconfirmed


def _confirm_written(
    object_type: str,
    written: dict[str, Any] | None,
    record: dict[str, Any] | None,
    result: dict[str, Any],
    problems: list[str],
) -> None:
    """Compare a read-back record with what was written, recording any field
    HiBob did not keep. A custom field is the usual reason, and is named."""
    if record is None:
        return
    unconfirmed = _unconfirmed_fields(object_type, written, record)
    if not unconfirmed:
        return
    result["unconfirmed_fields"] = unconfirmed
    detail = ", ".join(
        f"{field_id} (sent {info['sent']!r}, read back {info['read_back']!r})"
        for field_id, info in unconfirmed.items()
    )
    message = (
        f"HiBob accepted the write but these fields do not read back as sent: {detail}"
    )
    if any("/field_" in field_id for field_id in unconfirmed):
        message += (
            ". Custom fields may not be writable through HiBob's API; if so they "
            "must be set in HiBob itself"
        )
    problems.append(message)


def _position_query_label(entry: dict[str, Any]) -> str:
    """One line describing a position, for matching a free-text query."""
    display = entry.get("display") or {}
    values = entry.get("values") or {}
    parts: list[str] = []
    for field_id in POSITION_QUERY_FIELDS:
        label = display.get(field_id)
        if label is None:
            label = values.get(field_id)
        if label is not None and str(label).strip():
            parts.append(str(label).strip())
    return QUERY_LABEL_SEPARATOR.join(parts)


NEAR_MISSES_TO_NAME = 5


def _match_position_query(
    entries: list[dict[str, Any]], query: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """The positions a free-text query picks out, and the nearest misses.

    A query equal to one part of a position's label (its title, code or
    holder) matches that position alone. Otherwise every word must match;
    when nothing does, the labels of the closest positions by word overlap
    are returned instead, so the caller can refine the query.
    """
    labels = [_position_query_label(entry) for entry in entries]
    wanted = query.strip().lower()
    exact = [
        entry
        for entry, label in zip(entries, labels, strict=True)
        if any(
            part.strip().lower() == wanted
            for part in label.split(QUERY_LABEL_SEPARATOR)
        )
    ]
    if exact:
        return exact, []
    leaves = [
        {"id": f"entry-{index}", "name": label} for index, label in enumerate(labels)
    ]
    by_leaf = {leaf["id"]: entry for leaf, entry in zip(leaves, entries, strict=True)}
    matched = [
        by_leaf[leaf["id"]] for leaf in rank_matches(leaves, query, require_all=True)
    ]
    if matched:
        return matched, []
    near = rank_matches(leaves, query)[:NEAR_MISSES_TO_NAME]
    return [], [leaf["name"] for leaf in near]


def _serialize_filters(filters: list[SearchFilter] | None) -> list[dict[str, Any]]:
    return [f.to_payload() for f in (filters or [])]


def _search_body(
    fields: list[str],
    filters: list[SearchFilter] | None,
    include_human_readable: bool,
) -> dict[str, Any]:
    if not fields:
        raise ValueError(
            "At least one field ID is required. Call hibob_list_workforce_fields "
            "to discover available field IDs."
        )
    if len(fields) > 50:
        raise ValueError(
            f"HiBob accepts at most 50 fields per search; {len(fields)} were given."
        )
    return {
        "fields": list(fields),
        "filters": _serialize_filters(filters),
        "includeHumanReadable": include_human_readable,
    }


def _raw_search_entries(payload: Any, entries_key: str) -> tuple[Any, str | None]:
    """The entries of a search response, unflattened, and its next cursor."""
    if isinstance(payload, list):
        # Tolerate a bare list, as the position search endpoint returns.
        entries: Any = payload
        metadata: Any = None
    elif isinstance(payload, dict):
        # HiBob's live API keys the entries as "values"; its reference documents
        # a per-object key such as "positionOpeningEntries" instead. Accept both.
        entries = payload.get("values", payload.get(entries_key))
        metadata = payload.get("response_metadata")
    else:
        entries = None
        metadata = None
    next_cursor = metadata.get("next_cursor") if isinstance(metadata, dict) else None
    return entries, next_cursor


def _raw_rows(payload: Any, entries_key: str) -> list[dict[str, Any]]:
    """Just the entry dictionaries, for joining before they are flattened."""
    entries, _ = _raw_search_entries(payload, entries_key)
    return [entry for entry in entries or [] if isinstance(entry, dict)]


def _paged_search_result(payload: Any, entries_key: str) -> dict[str, Any]:
    """Shape a cursor-paginated search response for the caller."""
    entries, next_cursor = _raw_search_entries(payload, entries_key)

    flattened = flatten_search_entries(entries)
    result: dict[str, Any] = {"count": len(flattened), "entries": flattened}
    if next_cursor:
        result["next_cursor"] = next_cursor
        result["has_more"] = True
    else:
        result["has_more"] = False
    return result


async def _fetch_budgets(
    client: HiBobClient, fields: list[str], filters: list[SearchFilter]
) -> list[dict[str, Any]]:
    """Every budget the filters match, following the cursor to the last page."""
    rows: list[dict[str, Any]] = []
    seen_cursors: set[str] = set()
    cursor: str | None = None
    body = _search_body(fields, filters, True)
    for _ in range(MAX_BUDGET_SCAN_PAGES):
        pagination: dict[str, Any] = {"limit": BUDGET_PAGE_SIZE}
        if cursor:
            pagination["cursor"] = cursor
        payload = await client.search(
            BUDGET_SEARCH_PATH, {**body, "pagination": pagination}
        )
        rows.extend(_raw_rows(payload, "positionBudgetEntries"))
        _, cursor = _raw_search_entries(payload, "positionBudgetEntries")
        if not cursor or cursor in seen_cursors:
            break
        seen_cursors.add(cursor)
    return rows


# Enough to say which position owns a budget, and no more: the scan runs on
# every budget search, so it stays to three fields.
POSITION_INDEX_FIELDS = (
    "/position/id",
    "/position/name",
    POSITION_BUDGET_REF_FIELD,
)


async def _position_index_by_budget(
    client: HiBobClient,
) -> dict[str, dict[str, Any]]:
    """Map budget ID to owning position, from one scan of every position."""
    payload = await client.search(
        POSITION_SEARCH_PATH,
        _search_body(list(POSITION_INDEX_FIELDS), [MATCH_ALL_POSITIONS_FILTER], False),
    )
    return index_positions_by_budget(_raw_rows(payload, "positionEntries"))


async def _costed_rows(
    client: HiBobClient,
    position_filters: list[SearchFilter],
    include_human_readable: bool,
    *,
    every_budget: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Positions matching the filters, each merged with its budget.

    Two calls: the positions, then their budgets. The join happens here
    because a budget carries no position ID of its own. ``every_budget`` asks
    HiBob for all of them rather than naming each ID, which is what a
    company-wide roll-up wants and what keeps the filter from growing with
    the company.
    """
    position_fields = list(
        dict.fromkeys([*DEFAULT_POSITION_COST_FIELDS, POSITION_BUDGET_REF_FIELD])
    )
    payload = await client.search(
        POSITION_SEARCH_PATH,
        _search_body(position_fields, position_filters, include_human_readable),
    )
    position_rows = _raw_rows(payload, "positionEntries")

    refs: list[str] = []
    for row in position_rows:
        ref = cell_value(row, POSITION_BUDGET_REF_FIELD)
        if ref is not None:
            value = normalize_id(ref)
            if value not in refs:
                refs.append(value)

    budget_rows: list[dict[str, Any]] = []
    if every_budget:
        budget_rows = await _fetch_budgets(
            client, list(DEFAULT_BUDGET_COST_FIELDS), [MATCH_ALL_BUDGETS_FILTER]
        )
    elif refs:
        budget_rows = await _fetch_budgets(
            client,
            list(DEFAULT_BUDGET_COST_FIELDS),
            [SearchFilter(field_id=BUDGET_ID_FIELD, values=refs)],
        )
    return join_positions_to_budgets(position_rows, budget_rows)


def register_workforce_planning_tools(
    mcp: FastMCP,
    *,
    read_only: bool = False,
    client_factory: Any = get_client,
    list_cache: NamedListCache | None = None,
) -> None:
    """Register workforce planning tools on ``mcp``.

    When ``read_only`` is true only the read tools are registered, so a
    deployment can expose planning data without any ability to change it.
    Named lists are cached in ``list_cache`` (a fresh five-minute cache by
    default) across the form and named-list tools.
    """
    cache = list_cache if list_cache is not None else NamedListCache()

    def client() -> HiBobClient:
        return client_factory()

    # ------------------------------------------------------------------
    # Read tools
    # ------------------------------------------------------------------

    @mcp.tool(
        name="hibob_list_workforce_fields",
        annotations=ToolAnnotations(
            title="List HiBob workforce planning fields",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def hibob_list_workforce_fields(
        object_type: Annotated[
            ObjectTypeLiteral,
            Field(description="Which workforce planning object to describe."),
        ] = "position",
    ) -> str:
        """List the fields available on a HiBob workforce planning object.

        Call this before searching or creating anything: it returns the field
        IDs (such as '/position/fte') that every other tool in this server
        expects, along with each field's type and whether it is required.

        Args:
            object_type: 'position', 'positionOpening' or 'positionBudget'.

        Returns:
            str: JSON describing the available fields, as returned by HiBob.

        Examples:
            - "What can I set on a position?" -> object_type='position'
            - "What does a budget need?" -> object_type='positionBudget'
            - Don't use when: you are about to create something and need the
              fields together with their allowed values (use
              hibob_get_workforce_form, which also resolves the named lists).

        Rate limit: 50 requests/minute.
        """
        try:
            path = METADATA_PATHS[object_type]
            return _dump(await client().get(path))
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_get_workforce_form",
        annotations=ToolAnnotations(
            title="Get a HiBob workforce planning form",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def hibob_get_workforce_form(
        object_type: Annotated[
            ObjectTypeLiteral,
            Field(
                description=(
                    "Which object the form is for. 'position' also includes the "
                    "nested opening (required) and budget (optional) sections."
                )
            ),
        ] = "position",
        department: Annotated[
            str | None,
            Field(
                description=(
                    "Position form only: the department the position belongs to, "
                    "as a name or list item ID. Ask the user for this before "
                    "calling; it narrows the manager and job profile choices."
                )
            ),
        ] = None,
        job_profile: Annotated[
            str | None,
            Field(
                description=(
                    "Position form only: a rough description of the role, such as "
                    "a job title. Ask the user for this before calling."
                )
            ),
        ] = None,
        manager: Annotated[
            str | None,
            Field(
                description=(
                    "Position form only: who the position reports to, as the "
                    "manager's name, their position name (P-...) or position ID. "
                    "Usually known from the conversation."
                )
            ),
        ] = None,
        max_options: Annotated[
            int,
            Field(
                description=(
                    "Most options to inline per field. A Slack select allows 100."
                ),
                ge=1,
                le=1000,
            ),
        ] = MAX_OPTIONS_PER_LIST,
        include_archived_list_items: Annotated[
            bool,
            Field(
                description=(
                    "Also list archived list items. HiBob does not accept them "
                    "in new records, so leave this off for a create form."
                )
            ),
        ] = False,
    ) -> str:
        """Get everything needed to fill in a create form, in one call.

        Joins the object's field metadata (for a position: also its nested
        opening and budget) with the company's named lists, so every list
        field such as department, site or job profile arrives with its
        drop-down options and the IDs to submit. Fields that are not backed
        by a list come with their documented allowed values, and every field
        says whether it is required. Fields HiBob sets itself are listed
        separately so they are not mistaken for inputs.

        A form must be complete when it is generated, so every field's
        options are inlined, at most max_options each. Job profiles and
        manager positions are far larger than that, so for a position form
        ask the user first which department the position is in, roughly what
        the role is (a job title), and who it reports to, and pass those as
        department, job_profile and manager. They narrow those fields to the
        matching choices, and an unambiguous answer pre-fills the field. If a
        field still cannot be offered in full, the response carries
        "questions" instead of "sections": ask them, then call again with the
        answers.

        Use this before hibob_create_position, hibob_create_position_opening
        or hibob_create_position_budget. It replaces a chain of
        hibob_list_workforce_fields and hibob_get_company_named_lists calls.

        Args:
            object_type: 'position', 'positionOpening' or 'positionBudget'.
            department: Department name or ID (position form only).
            job_profile: Rough role description or title (position form only).
            manager: Manager's name, position name or ID (position form only).
            max_options: Most options to inline per field.
            include_archived_list_items: Include archived list items.

        Returns:
            str: JSON {"form": str, "submit_with": str, "instructions": [str],
            "sections": [{"object_type", "role", "argument", "required_fields",
            "fields": [{"id", "name", "type", "required", "options" or
            "allowed_values", "value"?, ...}], "read_only_fields": [...]}],
            "warnings": [str]}; or, when more is needed first, {"form",
            "submit_with", "questions": [{"field", "argument", "ask",
            "candidates"?}], "note"}; or an error message beginning with
            "Error:".

        Examples:
            - "I want to plan a new engineering position" -> ask which
              department, what role and who it reports to, then
              object_type='position', department='Engineering',
              job_profile='backend engineer', manager='Jane Doe'
            - "Add another opening to position 4821" ->
              object_type='positionOpening'

        Rate limit: metadata 50 requests/minute; one metadata request per
        section plus one named-lists request per list a field draws from.
        """
        try:
            layout = FORM_LAYOUTS[object_type]
            api = client()
            metadata = await asyncio.gather(
                *(api.get(METADATA_PATHS[kind]) for kind, _, _ in layout)
            )
            named_lists: dict[str, list[Any]] = {}
            warnings: list[str] = []
            list_ids = collect_list_ids(*metadata)
            if list_ids:
                named_lists, warnings = await _resolve_named_lists(
                    api, list_ids, include_archived_list_items, cache
                )
            overrides: dict[str, dict[str, Any]] = {}
            questions: list[dict[str, Any]] = []
            if object_type == OBJECT_TYPE_POSITION:
                overrides, questions = narrow_position_lists(
                    metadata[0],
                    named_lists,
                    department=department,
                    job_profile=job_profile,
                    manager=manager,
                    max_options=max_options,
                )
            result: dict[str, Any] = {
                "form": object_type,
                "submit_with": FORM_SUBMIT_TOOLS[object_type],
            }
            if questions:
                result["questions"] = questions
                result["note"] = (
                    "Ask the user these questions, then call again passing the "
                    "answers as the named arguments to get the form."
                )
            else:
                result["instructions"] = list(FORM_INSTRUCTIONS)
                result["sections"] = [
                    build_form_section(
                        section_type,
                        payload,
                        named_lists,
                        role=role,
                        argument=argument,
                        max_options=max_options,
                        overrides=overrides
                        if section_type == OBJECT_TYPE_POSITION
                        else None,
                    )
                    for (section_type, role, argument), payload in zip(
                        layout, metadata, strict=True
                    )
                ]
            if warnings:
                result["warnings"] = warnings
            return _dump(result)
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_get_company_named_lists",
        annotations=ToolAnnotations(
            title="Get HiBob company named lists",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def hibob_get_company_named_lists(
        list_name: Annotated[
            str | None,
            Field(
                description=(
                    "List to fetch, e.g. 'department' or 'site'. Omit to get only "
                    "the names and sizes of every list."
                )
            ),
        ] = None,
        include_archived: Annotated[
            bool,
            Field(
                description=(
                    "Also return archived items, which HiBob will not accept in "
                    "new records."
                )
            ),
        ] = False,
    ) -> str:
        """Look up the items of a HiBob named list, or see which lists exist.

        Position fields such as department, site and employment type must be
        set to a list item from HiBob's named lists rather than to free text.
        With a list_name this returns that list's items and the IDs that
        hibob_create_position and hibob_update_position expect. Without one it
        returns only the name and size of every list, because the full
        contents of every list can run to tens of megabytes.

        Args:
            list_name: The list to fetch, or None to list the available lists.
            include_archived: Include archived items.

        Returns:
            str: With list_name, JSON {"name": str, "count": int, "items":
            [{"id", "name", "archived"?, "children"?}]}. Without it, JSON
            {"count": int, "lists": [{"name": str, "items": int}], "note":
            str}. Errors are a message beginning with "Error:".

        Examples:
            - "Which departments exist?" -> list_name='department'
            - "What lists does HiBob have?" -> no arguments
            - Use before hibob_create_position to turn "Engineering" into its
              list item ID.
            - Use when hibob_get_workforce_form reports a field's options as
              truncated, to fetch that list in full.
        """
        try:
            api = client()
            if list_name and list_name.strip():
                name = list_name.strip()
                items = await _fetch_named_list(api, name, include_archived, cache)
                return _dump(
                    {
                        "name": name,
                        "count": count_list_items(items),
                        "items": shape_list_items(items),
                    }
                )
            lists = await _summarize_named_lists(api, include_archived, cache)
            return _dump(
                {
                    "count": len(lists),
                    "lists": lists,
                    "note": (
                        "Call again with list_name to get a list's items and the "
                        "IDs to submit."
                    ),
                }
            )
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_search_positions",
        annotations=ToolAnnotations(
            title="Search HiBob positions",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def hibob_search_positions(
        fields: Annotated[
            list[str],
            Field(
                description=(
                    "Field IDs to return, 1-50 of them, e.g. "
                    "['/position/id', '/position/name', '/position/status']."
                )
            ),
        ],
        filters: Annotated[
            list[SearchFilter] | None,
            Field(
                description=(
                    "Optional filters. Filterable fields: '/position/status', "
                    "'/position/name', '/position/hasOpenRequests', '/position/id'. "
                    "Omit to return every position."
                )
            ),
        ] = None,
        include_human_readable: Annotated[
            bool,
            Field(description="Also return display labels for each value."),
        ] = True,
        query: Annotated[
            str | None,
            Field(
                description=(
                    "Free text to match against each position's title, code, "
                    "department, site, job profile and holder, e.g. 'customer "
                    "success manager madrid' or 'Stina Grahn'. HiBob cannot filter "
                    "on those, so every position the filters allow is fetched and "
                    "matched here."
                )
            ),
        ] = None,
    ) -> str:
        """Search the company's positions.

        Returns one entry per matching position. Each entry has 'values'
        (the raw values, including the IDs needed by the update tools) and
        'display' (human-readable labels).

        This endpoint has no pagination, so always request only the fields you
        need and filter where possible in a large organization.

        Args:
            fields: Field IDs to return (1-50).
            filters: Optional filter clauses combined by HiBob.
            include_human_readable: Include display labels alongside raw values.

            query: Free text matched locally against title, code, department,
                site, job profile and holder.
        Returns:
            str: JSON of the form {"count": int, "entries": [{"values": {...},
            "display": {...}}]}, or an error message beginning with "Error:".

        Examples:
            - "Which positions are vacant?" -> fields=['/position/id',
              '/position/name'], filters=[{field_id: '/position/status',
              operator: 'equals', values: ['vacant']}]
            - Don't use when: you need opening-level detail such as expected
              start dates (use hibob_search_position_openings).

        Rate limit: 100 requests/minute.
        """
        try:
            query_text = (query or "").strip()
            search_fields = list(fields)
            if query_text:
                for field_id in POSITION_QUERY_FIELDS:
                    if field_id not in search_fields:
                        search_fields.append(field_id)
                search_fields = search_fields[:MAX_SEARCH_FIELDS]
            body = _search_body(
                search_fields,
                filters or [MATCH_ALL_POSITIONS_FILTER],
                include_human_readable or bool(query_text),
            )
            payload = await client().search(POSITION_SEARCH_PATH, body)
            entries = payload
            if isinstance(payload, dict):
                entries = (
                    payload.get("values")
                    or payload.get("positionEntries")
                    or payload.get("entries")
                )
            flattened = flatten_search_entries(entries)
            result: dict[str, Any] = {"count": len(flattened), "entries": flattened}
            if query_text:
                matched, near_misses = _match_position_query(flattened, query_text)
                result = {
                    "count": len(matched),
                    "entries": matched,
                    "query": query_text,
                    "scanned": len(flattened),
                }
                if near_misses:
                    result["note"] = (
                        "No position matches every word of the query. Nearest by "
                        "word overlap: " + "; ".join(near_misses)
                    )
            return _dump(result)
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_search_position_openings",
        annotations=ToolAnnotations(
            title="Search HiBob position openings",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def hibob_search_position_openings(
        fields: Annotated[
            list[str],
            Field(
                description=(
                    "Field IDs to return, 1-50, e.g. "
                    "['/positionOpening/id', '/positionOpening/status']."
                )
            ),
        ],
        filters: Annotated[
            list[SearchFilter] | None,
            Field(
                description=(
                    "Optional filters. Filterable fields: '/positionOpening/id', "
                    f"'/positionOpening/status' ({OPENING_STATUS_VALUES}), "
                    "'/positionOpening/positionOpeningName'. HiBob cannot filter "
                    "by '/positionOpening/positionId'; use "
                    "hibob_get_openings_for_positions for that. Omit to return "
                    "every opening."
                )
            ),
        ] = None,
        limit: Annotated[
            int, Field(description="Maximum entries per page.", ge=1, le=100)
        ] = 100,
        cursor: Annotated[
            str | None,
            Field(
                description=(
                    "Pass the 'next_cursor' from a previous call to get the next page."
                )
            ),
        ] = None,
        include_human_readable: Annotated[
            bool, Field(description="Also return display labels for each value.")
        ] = True,
    ) -> str:
        """Search position openings, the vacancies attached to positions.

        Openings carry the recruitment view of a position: expected start date,
        recruitment status and whether the seat is vacant, starting, filled or
        departing.

        Args:
            fields: Field IDs to return (1-50).
            filters: Optional filter clauses.
            limit: Page size, 1-100.
            cursor: Cursor from a previous page, or None to start.
            include_human_readable: Include display labels.

        Returns:
            str: JSON of the form {"count": int, "entries": [...],
            "has_more": bool, "next_cursor": str}. When "has_more" is true,
            call again passing "next_cursor" to retrieve the rest.

        Examples:
            - "Which openings are still vacant?" -> filters=[{field_id:
              '/positionOpening/status', operator: 'equals', values: ['vacant']}]
            - Don't use when: you want the openings of a particular position
              (use hibob_get_openings_for_positions, since HiBob cannot filter
              openings by position).

        Rate limit: 100 requests/minute.
        """
        try:
            body = _search_body(
                fields, filters or [MATCH_ALL_OPENINGS_FILTER], include_human_readable
            )
            pagination: dict[str, Any] = {"limit": limit}
            if cursor:
                pagination["cursor"] = cursor
            body["pagination"] = pagination
            payload = await client().search(OPENING_SEARCH_PATH, body)
            return _dump(_paged_search_result(payload, "positionOpeningEntries"))
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_get_openings_for_positions",
        annotations=ToolAnnotations(
            title="Get HiBob openings for positions",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def hibob_get_openings_for_positions(
        position_ids: Annotated[
            list[str | int],
            Field(description=POSITION_REFS_DESCRIPTION, min_length=1),
        ],
        fields: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Opening field IDs to return. '/positionOpening/id' and "
                    "'/positionOpening/positionId' are always included. Defaults "
                    "to the opening's name, status, recruitment status, expected "
                    "and actual start dates and who fills it."
                )
            ),
        ] = None,
        statuses: Annotated[
            list[OpeningStatusLiteral] | None,
            Field(
                description=(
                    "Only return openings in these statuses "
                    f"({OPENING_STATUS_VALUES}). Omit for every opening."
                )
            ),
        ] = None,
        include_human_readable: Annotated[
            bool, Field(description="Also return display labels for each value.")
        ] = True,
    ) -> str:
        """Get the openings that belong to one or more positions.

        HiBob's opening search can only filter by an opening's own ID, status
        or name, not by its parent position. This tool pages through every
        opening in the company (100 per request) and keeps those whose
        '/positionOpening/positionId' matches, doing the join here instead of
        in HiBob. Pass several positions at once to pay for that scan only
        once; a status filter is applied by HiBob and shortens it. A position
        may be given by numeric ID or by its name (P-...); names are resolved
        in one search first.

        Args:
            position_ids: Positions to look up, by ID or name.
            fields: Opening field IDs to return; the join fields are always
                added.
            statuses: Restrict to these opening statuses.
            include_human_readable: Include display labels alongside raw values.

        Returns:
            str: JSON {"count": int, "entries": [{"values": {...}, "display":
            {...}}], "counts_by_position": {"<position id>": int},
            "openings_scanned": int, "scan_complete": bool,
            "resolved_positions"?: {"<name>": "<position id>"}}, or an error
            message beginning with "Error:". Every entry carries
            '/positionOpening/positionId', so entries can be grouped by
            position; a position with no openings has a count of 0.

        Examples:
            - "Which openings does position 4821 have?" -> position_ids=['4821']
            - "Vacant openings under positions 12 and 15" ->
              position_ids=['12', '15'], statuses=['vacant']
            - Don't use when: filtering openings by their own status or name
              across the company (use hibob_search_position_openings).

        Rate limit: 100 requests/minute; this uses one request per 100
        openings in the company.
        """
        try:
            wanted, resolved = await _resolve_position_ids(client(), position_ids)
            requested = list(fields) if fields else list(DEFAULT_OPENING_FIELDS)
            search_fields = list(
                dict.fromkeys([OPENING_ID_FIELD, OPENING_POSITION_ID_FIELD, *requested])
            )
            if statuses:
                filters = [
                    SearchFilter(field_id=OPENING_STATUS_FIELD, values=list(statuses))
                ]
            else:
                filters = [MATCH_ALL_OPENINGS_FILTER]
            body = _search_body(search_fields, filters, include_human_readable)
            entries, complete = await _scan_openings(client(), body)
            matched, counts = _openings_for_positions(entries, wanted)
            result: dict[str, Any] = {
                "count": len(matched),
                "entries": matched,
                "counts_by_position": counts,
                "openings_scanned": len(entries),
                "scan_complete": complete,
            }
            if resolved:
                result["resolved_positions"] = resolved
            if not complete:
                result["warning"] = (
                    f"Stopped after {len(entries)} openings without reaching the "
                    "last page, so some openings may be missing. Narrow the scan "
                    "with 'statuses'."
                )
            return _dump(result)
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_get_positions_under",
        annotations=ToolAnnotations(
            title="Get HiBob positions under a position",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def hibob_get_positions_under(
        position: Annotated[
            str,
            Field(
                description=(
                    "The position at the top: a position ID, a position name such "
                    "as 'P-0000000157', the HiBob employee ID or work email of the "
                    "person holding it, or that person's name."
                ),
                min_length=1,
            ),
        ],
        depth: Annotated[
            int | None,
            Field(
                description=(
                    "How many levels down to include; 1 for direct reports only. "
                    "Omit for every level."
                ),
                ge=1,
            ),
        ] = None,
        statuses: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Only return positions in these statuses (filled, vacant, "
                    "starting, ...). The tree is still walked in full, so the "
                    "shape reported stays right."
                )
            ),
        ] = None,
    ) -> str:
        """List the positions that report up to a position.

        Answers "which positions are under me, and which are filled?". HiBob
        cannot filter positions by manager or by holder, so this fetches every
        position in one search and walks the reporting tree here. The top
        position is found by ID, by position name, by the holder's employee
        ID, by the holder's work email (one narrow lookup of the employee ID,
        nothing else) or by the holder's name; a name that fits several people
        returns candidates instead. Everything beneath it is then listed depth first,
        each position with its status (filled, vacant, starting), who fills
        it, and its own manager position so the tree can be redrawn.

        Args:
            position: Position ID, position name, employee ID, work email or
                holder's name.
            depth: Levels to include; 1 for direct reports. None for all.
            statuses: Keep only these statuses in the listing.

        Returns:
            str: JSON {"root": {...}, "resolved_by": str, "count": int,
            "direct_reports": int, "max_depth": int, "counts_by_status":
            {status: int}, "positions": [{"id", "name", "status", "holder",
            "holder_id", "department", "job_profile", "site",
            "manager_position_id", "has_open_requests", "depth"}]}; or
            {"query", "candidates": [...], "note"} when several positions
            match; or an error message beginning with "Error:".

        Examples:
            - "Which positions are under me?" -> position='<the user's name or
              HiBob employee ID>'
            - "Which of Anna's positions are vacant?" ->
              position='Anna Serafin Valle', statuses=['vacant']
            - For the openings behind the listed positions, pass their IDs to
              hibob_get_openings_for_positions.

        Rate limit: 100 requests/minute; this uses one.
        """
        try:
            api = client()
            query = position.strip()
            employee_ids: list[str] = []
            if "@" in query:
                employee_ids = await _employee_ids_for_email(api, query.lower())
                if not employee_ids:
                    raise ValueError(f"No HiBob employee has the email {query!r}.")
            body = _search_body(
                list(HIERARCHY_FIELDS), [MATCH_ALL_POSITIONS_FILTER], True
            )
            payload = await api.search(POSITION_SEARCH_PATH, body)
            if isinstance(payload, dict):
                payload = payload.get("values")
            rows = payload if isinstance(payload, list) else []
            positions = [shape_position(row) for row in rows if isinstance(row, dict)]
            if employee_ids:
                wanted = set(employee_ids)
                matches = [p for p in positions if p.get("holder_id") in wanted]
                resolved_by = RESOLVED_BY_EMAIL
                if not matches:
                    raise ValueError(
                        f"{query!r} is a HiBob employee but holds no position in "
                        "the workforce plan."
                    )
            else:
                matches, resolved_by = resolve_root(positions, query)
            if not matches:
                raise ValueError(
                    f"No position matches {position!r}. Give a position ID, a "
                    "position name such as 'P-0000000157', the holder's HiBob "
                    "employee ID, or the holder's name as it appears in HiBob."
                )
            if len(matches) > 1:
                return _dump(
                    {
                        "query": position,
                        "candidates": matches,
                        "note": (
                            "Several positions match; call again with the "
                            "position ID of the one meant."
                        ),
                    }
                )
            root = matches[0]
            entries = positions_under(positions, root["id"], depth=depth)
            shape = summarize_tree(entries)
            if statuses:
                wanted = {status.strip().lower() for status in statuses}
                entries = [
                    entry
                    for entry in entries
                    if str(entry.get("status", "")).lower() in wanted
                ]
            result: dict[str, Any] = {
                "root": root,
                "resolved_by": resolved_by,
                "count": len(entries),
                **shape,
                "counts_by_status": counts_by_status(entries),
                "positions": entries,
            }
            return _dump(result)
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_search_position_budgets",
        annotations=ToolAnnotations(
            title="Search HiBob position budgets",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def hibob_search_position_budgets(
        fields: Annotated[
            list[str],
            Field(
                description=(
                    "Field IDs to return, 1-50, e.g. "
                    "['/positionBudget/expectedBaseSalaryCurrencyValue']."
                )
            ),
        ],
        filters: Annotated[
            list[SearchFilter] | None,
            Field(description="Optional filter clauses on budget fields."),
        ] = None,
        limit: Annotated[
            int,
            Field(description="Maximum entries per page, up to 1000.", ge=1, le=1000),
        ] = 100,
        cursor: Annotated[
            str | None, Field(description="Cursor from a previous page.")
        ] = None,
        include_human_readable: Annotated[
            bool, Field(description="Also return display labels for each value.")
        ] = True,
        include_position: Annotated[
            bool,
            Field(
                description=(
                    "Name the position each budget belongs to. Costs one extra "
                    "request. Turn off only for a pure total, where the link is "
                    "not needed."
                )
            ),
        ] = True,
    ) -> str:
        """Search position budgets: planned salary and cost figures.

        A budget record carries no reference to the position it belongs to.
        HiBob's only link runs the other way, as "/position/budget" on the
        position, so a budget fetched here cannot be attributed to anything on
        its own. This tool therefore reads that reference off every position
        and reports the owner as each entry's "position". Do not conclude from
        a budget's own fields that its position cannot be identified.

        That link is synthesised here, which is why it sits beside "values"
        rather than among the field IDs: HiBob cannot filter or sort on it.
        Budgets can only be filtered by "/positionBudget/id" and
        "/positionBudget/proRatedCostPercentage"; any other field is rejected.

        To price named positions, prefer hibob_get_position_costs, which does
        the join in the useful direction.

        Args:
            fields: Field IDs to return (1-50).
            filters: Optional filter clauses.
            limit: Page size, 1-1000. HiBob rejects anything larger.
            cursor: Cursor from a previous page, or None to start.
            include_human_readable: Include display labels.
            include_position: Name the owning position on each entry.

        Returns:
            str: JSON of the form {"count": int, "entries": [{"values": {...},
            "display": {...}, "position": {"id": str, "name": str} | null}],
            "has_more": bool, "next_cursor": str}. A "position" of null means
            no position references that budget. If the owner lookup fails the
            budgets are still returned, with "position_link_error" saying why,
            and no "position" key -- which is not the same as null.

        Examples:
            - "Cost of every budget in the plan" -> fields with the cost
              figures; read each entry's "position" to see whose it is.
            - Don't use when: you already know which positions you care about
              (use hibob_get_position_costs), or you want a total broken down
              by department (use hibob_summarize_position_costs).

        Rate limit: 100 requests/minute; two requests unless include_position
        is false.
        """
        try:
            body = _search_body(
                fields, filters or [MATCH_ALL_BUDGETS_FILTER], include_human_readable
            )
            pagination: dict[str, Any] = {"limit": limit}
            if cursor:
                pagination["cursor"] = cursor
            body["pagination"] = pagination
            payload = await client().search(BUDGET_SEARCH_PATH, body)
            result = _paged_search_result(payload, "positionBudgetEntries")
            if include_position and result["entries"]:
                try:
                    index = await _position_index_by_budget(client())
                except Exception as exc:
                    # The budgets were fetched; losing the owner lookup must
                    # not discard them, but it must not pass unremarked either.
                    result["position_link_error"] = format_exception(exc).removeprefix(
                        "Error: "
                    )
                else:
                    result["entries"] = attach_positions(result["entries"], index)
            return _dump(result)
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_get_position_costs",
        annotations=ToolAnnotations(
            title="Get HiBob position costs",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def hibob_get_position_costs(
        position_ids: Annotated[
            list[str | int],
            Field(description=POSITION_REFS_DESCRIPTION, min_length=1),
        ],
        include_human_readable: Annotated[
            bool, Field(description="Also return display labels for each value.")
        ] = True,
    ) -> str:
        """Get what each of these positions costs: salary, total and prorated.

        Cost is not a field on a position. It lives on a separate
        positionBudget object, and the only link is '/position/budget' on the
        position, which holds the budget's ID -- a budget carries no position
        ID of its own. This tool fetches the positions, reads that reference
        off each one, fetches exactly those budgets and joins them, so one
        call answers "what does this position cost?".

        Args:
            position_ids: Positions to price, by numeric ID or name (P-...).
            include_human_readable: Include display labels alongside raw values.

        Returns:
            str: JSON {"count": int, "entries": [{"values": {...}, "display":
            {...}}], "positions_without_budget": ["<position id>"]}, or an
            error message beginning with "Error:". Each entry carries the
            position's own fields and its budget's cost fields together; money
            arrives as {"value": number, "currency": str}.

        Examples:
            - "What does position 4821 cost?" -> position_ids=['4821']
            - "Cost of these three seats" -> position_ids=['12','15','19']
            - Don't use when: rolling cost up across a department or the whole
              company (use hibob_summarize_position_costs), which needs no IDs.

        Rate limit: 100 requests/minute; this uses two requests.
        """
        try:
            wanted, resolved = await _resolve_position_ids(client(), position_ids)
            merged, unbudgeted = await _costed_rows(
                client(),
                [SearchFilter(field_id="/position/id", values=wanted)],
                include_human_readable,
            )
            entries = flatten_search_entries(merged)
            # A position HiBob did not return is named rather than left out:
            # silence is what sends callers looking for cost in the first place.
            found = {
                normalize_id(cell_value(row, "/position/id")) for row in merged
            } | set(unbudgeted)
            result: dict[str, Any] = {
                "count": len(entries),
                "entries": entries,
                "positions_without_budget": unbudgeted,
                "positions_not_found": [pid for pid in wanted if pid not in found],
            }
            if resolved:
                result["resolved_positions"] = resolved
            return _dump(result)
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_summarize_position_costs",
        annotations=ToolAnnotations(
            title="Summarize HiBob position costs",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def hibob_summarize_position_costs(
        group_by: Annotated[
            GroupByLiteral | None,
            Field(
                description=(
                    f"Dimension to break the total down by ({GROUP_BY_VALUES}). "
                    "Omit for a single company-wide total."
                )
            ),
        ] = None,
        statuses: Annotated[
            list[PositionStatusLiteral] | None,
            Field(
                description=(
                    f"Only count positions in these statuses "
                    f"({POSITION_STATUS_VALUES}). Omit for every position."
                )
            ),
        ] = None,
    ) -> str:
        """Roll planned position cost up across the company or a department.

        HiBob can neither filter nor group by any cost field, so this fetches
        every position and every budget (two calls), joins them on
        '/position/budget' and aggregates here.

        Only the converted figures are totalled. HiBob reports each position's
        total in its own local currency, so adding those together across
        countries would produce a meaningless number; the converted values
        share the company's reporting currency and are what can be summed.

        Args:
            group_by: Break the total down by this dimension, or None for one
                company-wide total.
            statuses: Restrict to positions in these statuses.

        Returns:
            str: JSON {"position_count": int, "currency": str,
            "total_converted_cost": float, "groups": [{"group": str,
            "position_count": int, "total_converted_cost": float}],
            "positions_without_budget": ["<position id>"]}, or an error
            message beginning with "Error:". If converted costs ever come back
            in more than one currency, "total_converted_cost" is null and
            "totals_by_currency" carries a total per currency instead.

        Examples:
            - "What is our planned headcount cost?" -> no arguments
            - "Budgeted cost by department" -> group_by='department'
            - "Cost of everything still vacant" -> statuses=['vacant']
            - Don't use when: you need the cost of specific positions (use
              hibob_get_position_costs).

        Rate limit: 100 requests/minute; this uses two requests.
        """
        try:
            if statuses:
                position_filters = [
                    SearchFilter(field_id="/position/status", values=list(statuses))
                ]
            else:
                position_filters = [MATCH_ALL_POSITIONS_FILTER]
            merged, unbudgeted = await _costed_rows(
                client(), position_filters, True, every_budget=True
            )
            summary = summarize_costs(merged, group_by)
            summary["positions_without_budget"] = unbudgeted
            return _dump(summary)
        except Exception as exc:
            return format_exception(exc)

    if read_only:
        return

    # ------------------------------------------------------------------
    # Write tools
    # ------------------------------------------------------------------

    @mcp.tool(
        name="hibob_create_position",
        annotations=ToolAnnotations(
            title="Create a HiBob position",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def hibob_create_position(
        position_fields: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Position fields as a flat mapping, e.g. "
                    '{"/position/effectiveDate": "2026-09-01", "/position/fte": 100, '
                    '"/position/department": "<list item ID>", "/position/site": 123, '
                    '"/position/jobProfile": 456}. Required: effectiveDate, fte, '
                    "department, site, jobProfile."
                )
            ),
        ],
        opening_fields: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Fields for the position's first opening. Required: "
                    '"/positionOpening/expectedStartDate". Optional: '
                    "positionOpeningName, recruitmentStatus."
                )
            ),
        ],
        budget_fields: Annotated[
            dict[str, Any] | None,
            Field(
                description=(
                    "Optional budget. If given, requires "
                    '"/positionBudget/salaryPayPeriod" and '
                    '"/positionBudget/currency".'
                )
            ),
        ] = None,
    ) -> str:
        """Create a planned position, with its opening and optional budget.

        A position is a budgeted seat in the plan; every position must be
        created with an opening, which is the vacancy to be filled. Field
        values that reference HiBob lists (department, site, job profile) must
        be the list item IDs - resolve them first with
        hibob_get_company_named_lists and hibob_list_workforce_fields.

        Creates one position per call. Required fields are checked before the
        request is sent, because HiBob allows only ten write calls per minute.

        Args:
            position_fields: Flat mapping of position field IDs to values.
            opening_fields: Flat mapping for the nested opening.
            budget_fields: Optional flat mapping for the nested budget.

        Returns:
            str: JSON {"id": int, "positionOpeningId": int, "verified": bool,
            "position": {...}, "opening": {...}} - the new IDs plus the position
            and opening read back from HiBob; "verification_error" explains a
            failed read-back, which does not mean the write failed. Or an
            error message beginning with "Error:".

        Examples:
            - "Plan a new engineer starting in September" -> position_fields
              with effectiveDate/fte/department/site/jobProfile plus
              opening_fields with expectedStartDate.
            - Don't use when: adding a second vacancy to an existing position
              (use hibob_create_position_opening).

        Rate limit: 10 requests/minute.
        """
        try:
            validate_required_keys(
                OBJECT_TYPE_POSITION, position_fields, REQUIRED_POSITION_FIELDS
            )
            validate_required_keys(
                OBJECT_TYPE_OPENING, opening_fields, REQUIRED_OPENING_FIELDS
            )
            if budget_fields:
                validate_required_keys(
                    OBJECT_TYPE_BUDGET, budget_fields, REQUIRED_BUDGET_FIELDS
                )
            body = build_items_envelope(
                OBJECT_TYPE_POSITION,
                position_fields,
                opening=opening_fields,
                budget=budget_fields or None,
            )
            api = client()
            created = await api.post(POSITIONS_PATH, body)
            result: dict[str, Any] = (
                dict(created) if isinstance(created, dict) else {"response": created}
            )
            problems: list[str] = []
            position_id = result.get("id")
            opening_id = result.get("positionOpeningId")
            if position_id is None and opening_id is None:
                problems.append("HiBob's response did not include the new IDs")
            written = {
                OBJECT_TYPE_POSITION: position_fields,
                OBJECT_TYPE_OPENING: opening_fields,
            }
            if position_id is not None:
                record, problem = await _verify(
                    f"position {position_id}",
                    _read_back(
                        api,
                        POSITION_SEARCH_PATH,
                        "/position/id",
                        position_id,
                        _read_back_fields(
                            OBJECT_TYPE_POSITION,
                            VERIFY_POSITION_FIELDS,
                            position_fields,
                        ),
                        paginated=False,
                    ),
                )
                if record is not None:
                    result["position"] = record
                    _confirm_written(
                        OBJECT_TYPE_POSITION,
                        written[OBJECT_TYPE_POSITION],
                        record,
                        result,
                        problems,
                    )
                if problem:
                    problems.append(problem)
            if opening_id is not None:
                record, problem = await _verify(
                    f"opening {opening_id}",
                    _read_back(
                        api,
                        OPENING_SEARCH_PATH,
                        OPENING_ID_FIELD,
                        opening_id,
                        _read_back_fields(
                            OBJECT_TYPE_OPENING, VERIFY_OPENING_FIELDS, opening_fields
                        ),
                        paginated=True,
                    ),
                )
                if record is not None:
                    result["opening"] = record
                    _confirm_written(
                        OBJECT_TYPE_OPENING,
                        written[OBJECT_TYPE_OPENING],
                        record,
                        result,
                        problems,
                    )
                if problem:
                    problems.append(problem)
            return _dump(_finish_write(result, problems))
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_update_position",
        annotations=ToolAnnotations(
            title="Update a HiBob position",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def hibob_update_position(
        position_id: Annotated[
            str, Field(description=POSITION_REF_DESCRIPTION, min_length=1)
        ],
        fields: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Fields to change, as a flat mapping. Updatable: name, "
                    "effectiveDate, managerPositionId, positionType, fte, "
                    "employmentType, department, site, jobProfile, reason, and "
                    "custom fields (/position/field_<number>) whose IDs come from "
                    "hibob_get_workforce_form."
                )
            ),
        ],
    ) -> str:
        """Change details of an existing position.

        Only the fields supplied are modified. The position may be given by
        numeric ID or by its name (P-...). Custom fields (/position/field_<number>,
        such as a "Locations for hiring" list) are passed through to HiBob
        unverified: the result lists them as "undocumented_fields", and if
        HiBob rejects the update or quietly drops one of them, the error or
        "unconfirmed_fields" says so.

        Args:
            position_id: The position, by numeric ID or name.
            fields: Flat mapping of field IDs to new values.

        Returns:
            str: JSON {"status": "updated", "position_id": str, "verified":
            bool, "position": {...},
            "undocumented_fields"?: [...], "unconfirmed_fields"?: {...}} with
            the position read back from HiBob, including the fields just
            changed; "verification_error" explains a failed read-back or a
            field HiBob did not keep, neither of which means the update
            failed. Or an error message beginning with "Error:".

        Examples:
            - "Move that position's start to October" -> fields with
              '/position/effectiveDate'.

        Rate limit: 10 requests/minute.
        """
        try:
            if not fields:
                raise ValueError("Provide at least one field to update.")
            normalized = [
                normalize_field_key(OBJECT_TYPE_POSITION, key) for key in fields
            ]
            read_only = sorted(
                field_id
                for field_id in normalized
                if field_id in READ_ONLY_FIELDS[OBJECT_TYPE_POSITION]
            )
            if read_only:
                raise ValueError(
                    "Field(s) set by HiBob and not updatable: "
                    f"{', '.join(read_only)}. Updatable fields are "
                    f"{', '.join(sorted(UPDATABLE_POSITION_FIELDS))}, plus custom "
                    "fields such as /position/field_<number>."
                )
            undocumented = sorted(
                field_id
                for field_id in normalized
                if field_id not in UPDATABLE_POSITION_FIELDS
            )
            body = build_items_envelope(OBJECT_TYPE_POSITION, fields)
            api = client()
            position = await _resolve_position(api, position_id, lookup_ids=False)
            path = f"{POSITIONS_PATH}/{position.id}"
            try:
                patched = await api.patch(path, body)
            except HiBobApiError as exc:
                if undocumented:
                    raise HiBobApiError(
                        f"{exc} The update included fields outside HiBob's "
                        "documented position payload "
                        f"({', '.join(undocumented)}). HiBob may not accept custom "
                        "fields through its API; if so they must be set in HiBob "
                        "itself.",
                        status_code=exc.status_code,
                        hibob_key=exc.hibob_key,
                        hibob_error=exc.hibob_error,
                    ) from exc
                raise
            result: dict[str, Any] = {"status": "updated", "position_id": position.id}
            if isinstance(patched, dict) and patched:
                result["response"] = patched
            if undocumented:
                result["undocumented_fields"] = undocumented
            record, problem = await _verify(
                position.describe(),
                _read_back(
                    api,
                    POSITION_SEARCH_PATH,
                    "/position/id",
                    position.id,
                    _read_back_fields(
                        OBJECT_TYPE_POSITION, VERIFY_POSITION_FIELDS, fields
                    ),
                    paginated=False,
                ),
            )
            problems = [problem] if problem else []
            if record is not None:
                result["position"] = record
                _confirm_written(OBJECT_TYPE_POSITION, fields, record, result, problems)
            return _dump(_finish_write(result, problems))
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_cancel_position",
        annotations=ToolAnnotations(
            title="Cancel a HiBob position",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def hibob_cancel_position(
        position_id: Annotated[
            str, Field(description=POSITION_REF_DESCRIPTION, min_length=1)
        ],
    ) -> str:
        """Cancel a planned position, removing it from the workforce plan.

        HiBob refuses to cancel a position that is currently filled; check
        '/position/status' with hibob_search_positions first. Cancelling cannot
        be undone through this API, so confirm the position before calling.
        It may be given by numeric ID or by its name (P-...).

        Args:
            position_id: The position, by numeric ID or name.

        Returns:
            str: JSON confirming the cancellation, or an error message
            beginning with "Error:".

        Rate limit: 10 requests/minute.
        """
        try:
            api = client()
            position = await _resolve_position(api, position_id, lookup_ids=False)
            path = f"{POSITIONS_PATH}/{position.id}/cancel"
            result = await api.patch(path)
            return _dump(result or {"status": "cancelled", "positionId": position.id})
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_create_position_opening",
        annotations=ToolAnnotations(
            title="Create a HiBob position opening",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def hibob_create_position_opening(
        position_id: Annotated[
            str, Field(description=POSITION_REF_DESCRIPTION, min_length=1)
        ],
        fields: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Opening fields as a flat mapping. Required: "
                    '"/positionOpening/expectedStartDate". Optional: '
                    'positionOpeningName, recruitmentStatus ("open", "onHold", '
                    '"closed").'
                )
            ),
        ],
    ) -> str:
        """Add a vacancy to an existing position.

        Args:
            position_id: The parent position, by numeric ID or name (P-...).
            fields: Flat mapping of opening field IDs to values.

        Returns:
            str: JSON {"id": int, "positionOpeningId": int, "verified": bool,
            "opening": {...}} - HiBob's IDs (id is the position's) plus the
            opening read back, with its parent confirmed to be position_id;
            "verification_error" explains a failed read-back or a parent
            mismatch, neither of which means the write failed. Or an error
            message beginning with "Error:".

        Rate limit: 10 requests/minute.
        """
        try:
            validate_required_keys(OBJECT_TYPE_OPENING, fields, REQUIRED_OPENING_FIELDS)
            body = build_items_envelope(OBJECT_TYPE_OPENING, fields)
            api = client()
            position = await _resolve_position(api, position_id, lookup_ids=False)
            path = f"{POSITIONS_PATH}/{position.id}/position-openings"
            created = await api.post(path, body)
            result: dict[str, Any] = (
                dict(created) if isinstance(created, dict) else {"response": created}
            )
            result["position_id"] = position.id
            opening_id = result.get("positionOpeningId", result.get("id"))
            problems: list[str] = []
            if opening_id is None:
                problems.append("HiBob's response did not include the new opening ID")
            else:
                record, problem = await _verify(
                    f"opening {opening_id}",
                    _read_back(
                        api,
                        OPENING_SEARCH_PATH,
                        OPENING_ID_FIELD,
                        opening_id,
                        _read_back_fields(
                            OBJECT_TYPE_OPENING, VERIFY_OPENING_FIELDS, fields
                        ),
                        paginated=True,
                    ),
                )
                if problem:
                    problems.append(problem)
                if record is not None:
                    result["opening"] = record
                    _confirm_written(
                        OBJECT_TYPE_OPENING, fields, record, result, problems
                    )
                    parent = normalize_id(
                        record["values"].get(OPENING_POSITION_ID_FIELD)
                    )
                    if parent != position.id:
                        problems.append(
                            f"opening {opening_id} belongs to position {parent}, "
                            f"not {position.id}"
                        )
            return _dump(_finish_write(result, problems))
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_update_position_opening",
        annotations=ToolAnnotations(
            title="Update a HiBob position opening",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def hibob_update_position_opening(
        position_id: Annotated[
            str, Field(description=POSITION_REF_DESCRIPTION, min_length=1)
        ],
        opening_id: Annotated[
            str, Field(description=OPENING_REF_DESCRIPTION, min_length=1)
        ],
        fields: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Fields to change, e.g. "
                    '{"/positionOpening/recruitmentStatus": "onHold"}.'
                )
            ),
        ],
    ) -> str:
        """Change an existing opening, such as its expected start date or
        recruitment status.

        Both may be given by numeric ID or by name (P-... and O-...). The
        opening is looked up first, and an opening that belongs to a different
        position than the one given is refused before anything is written.

        Args:
            position_id: The parent position, by numeric ID or name.
            opening_id: The opening, by numeric ID or name.
            fields: Flat mapping of field IDs to new values.

        Returns:
            str: JSON {"status": "updated", "position_id": str, "opening_id":
            str, "verified": bool, "opening": {...}}, or an error message
            beginning with "Error:".

        Rate limit: 10 requests/minute.
        """
        try:
            if not fields:
                raise ValueError("Provide at least one field to update.")
            body = build_items_envelope(OBJECT_TYPE_OPENING, fields)
            api = client()
            position = await _resolve_position(api, position_id, lookup_ids=False)
            opening = await _resolve_opening(api, opening_id)
            check_opening_parent(opening, position)
            path = f"{POSITIONS_PATH}/{position.id}/position-openings/{opening.id}"
            patched = await api.patch(path, body)
            result: dict[str, Any] = {
                "status": "updated",
                "position_id": position.id,
                "opening_id": opening.id,
            }
            if isinstance(patched, dict) and patched:
                result["response"] = patched
            record, problem = await _verify(
                opening.describe(),
                _read_back(
                    api,
                    OPENING_SEARCH_PATH,
                    OPENING_ID_FIELD,
                    opening.id,
                    _read_back_fields(
                        OBJECT_TYPE_OPENING, VERIFY_OPENING_FIELDS, fields
                    ),
                    paginated=True,
                ),
            )
            problems = [problem] if problem else []
            if record is not None:
                result["opening"] = record
                _confirm_written(OBJECT_TYPE_OPENING, fields, record, result, problems)
            return _dump(_finish_write(result, problems))
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_delete_position_opening",
        annotations=ToolAnnotations(
            title="Delete a HiBob position opening",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def hibob_delete_position_opening(
        position_id: Annotated[
            str, Field(description=POSITION_REF_DESCRIPTION, min_length=1)
        ],
        opening_id: Annotated[
            str, Field(description=OPENING_REF_DESCRIPTION, min_length=1)
        ],
    ) -> str:
        """Permanently remove an opening from a position.

        This deletes the vacancy record in HiBob and cannot be undone through
        this API. Both may be given by numeric ID or by name (P-... and O-...);
        the opening is looked up first, and one that belongs to a different
        position than the one given is refused before anything is deleted.

        Args:
            position_id: The parent position, by numeric ID or name.
            opening_id: The opening, by numeric ID or name.

        Returns:
            str: JSON confirming the deletion, or an error message beginning
            with "Error:".

        Rate limit: 10 requests/minute.
        """
        try:
            api = client()
            position = await _resolve_position(api, position_id, lookup_ids=False)
            opening = await _resolve_opening(api, opening_id)
            check_opening_parent(opening, position)
            path = f"{POSITIONS_PATH}/{position.id}/position-openings/{opening.id}"
            result = await api.delete(path)
            return _dump(
                result
                or {
                    "status": "deleted",
                    "positionId": position.id,
                    "positionOpeningId": opening.id,
                }
            )
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_create_position_budget",
        annotations=ToolAnnotations(
            title="Create a HiBob position budget",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def hibob_create_position_budget(
        position_id: Annotated[
            str, Field(description=POSITION_REF_DESCRIPTION, min_length=1)
        ],
        fields: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Budget fields as a flat mapping. Required: "
                    '"/positionBudget/salaryPayPeriod" (e.g. "Annual", '
                    '"Monthly") and "/positionBudget/currency" (e.g. "GBP"). '
                    "Optional: expectedBaseSalaryCurrencyValue, "
                    "totalPositionCostCurrencyValue, "
                    "expectedVariablePayCurrencyValue, variablePayPeriod."
                )
            ),
        ],
    ) -> str:
        """Attach a salary and cost budget to a position.

        The position may be given by numeric ID or by its name (P-...). A
        position holds one budget, so one that already has a budget is
        refused; change it with hibob_update_position_budget instead.

        Args:
            position_id: The position, by numeric ID or name.
            fields: Flat mapping of budget field IDs to values.

        Returns:
            str: JSON {"positionBudgetId": int, "verified": bool, "budget":
            {...}} with the budget read back from HiBob; "verification_error"
            explains a failed read-back, which does not mean the write failed.
            Or an error message beginning with "Error:".

        Examples:
            - "Budget 65k a year for that role" -> fields with
              expectedBaseSalaryCurrencyValue, salaryPayPeriod and currency.

        Rate limit: 10 requests/minute.
        """
        try:
            validate_required_keys(OBJECT_TYPE_BUDGET, fields, REQUIRED_BUDGET_FIELDS)
            body = build_items_envelope(OBJECT_TYPE_BUDGET, fields)
            api = client()
            position = await _resolve_position(api, position_id, lookup_ids=True)
            refuse_existing_budget(position)
            path = f"{POSITIONS_PATH}/{position.id}/position-budget"
            created = await api.post(path, body)
            result: dict[str, Any] = (
                dict(created) if isinstance(created, dict) else {"response": created}
            )
            result["position_id"] = position.id
            budget_id = result.get("positionBudgetId", result.get("id"))
            problems: list[str] = []
            if budget_id is None:
                problems.append("HiBob's response did not include the new budget ID")
            else:
                record, problem = await _verify(
                    f"budget {budget_id}",
                    _read_back(
                        api,
                        BUDGET_SEARCH_PATH,
                        "/positionBudget/id",
                        budget_id,
                        _read_back_fields(
                            OBJECT_TYPE_BUDGET, VERIFY_BUDGET_FIELDS, fields
                        ),
                        paginated=True,
                    ),
                )
                if record is not None:
                    result["budget"] = record
                    _confirm_written(
                        OBJECT_TYPE_BUDGET, fields, record, result, problems
                    )
                if problem:
                    problems.append(problem)
            return _dump(_finish_write(result, problems))
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_update_position_budget",
        annotations=ToolAnnotations(
            title="Update a HiBob position budget",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def hibob_update_position_budget(
        position_id: Annotated[
            str, Field(description=POSITION_REF_DESCRIPTION, min_length=1)
        ],
        fields: Annotated[
            dict[str, Any],
            Field(description="Budget fields to change, as a flat mapping."),
        ],
        budget_id: Annotated[
            str | None,
            Field(
                description=(
                    "ID of the budget to update. Optional: a position has one "
                    "budget, which is found from the position. If given, it "
                    "must be that budget."
                )
            ),
        ] = None,
    ) -> str:
        """Change an existing position budget.

        The budget is found from the position, since a position carries its
        budget's ID and a budget has no name of its own. A budget_id that
        belongs to a different position is refused before anything is sent.

        Args:
            position_id: The position, by numeric ID or name.
            fields: Flat mapping of field IDs to new values.
            budget_id: The budget's ID, if the caller wants it checked.

        Returns:
            str: JSON confirming the update, or an error message beginning with
            "Error:".

        Rate limit: 10 requests/minute.
        """
        try:
            if not fields:
                raise ValueError("Provide at least one field to update.")
            body = build_items_envelope(OBJECT_TYPE_BUDGET, fields)
            api = client()
            position = await _resolve_position(api, position_id, lookup_ids=True)
            budget = budget_to_write(position, budget_id)
            path = f"{POSITIONS_PATH}/{position.id}/position-budget/{budget}"
            patched = await api.patch(path, body)
            result: dict[str, Any] = {
                "status": "updated",
                "position_id": position.id,
                "budget_id": budget,
            }
            if isinstance(patched, dict) and patched:
                result["response"] = patched
            record, problem = await _verify(
                f"budget {budget}",
                _read_back(
                    api,
                    BUDGET_SEARCH_PATH,
                    "/positionBudget/id",
                    budget,
                    _read_back_fields(OBJECT_TYPE_BUDGET, VERIFY_BUDGET_FIELDS, fields),
                    paginated=True,
                ),
            )
            problems = [problem] if problem else []
            if record is not None:
                result["budget"] = record
                _confirm_written(OBJECT_TYPE_BUDGET, fields, record, result, problems)
            return _dump(_finish_write(result, problems))
        except Exception as exc:
            return format_exception(exc)
