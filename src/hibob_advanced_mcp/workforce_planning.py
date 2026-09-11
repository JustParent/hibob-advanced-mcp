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
    validate_allowed_keys,
    validate_required_keys,
)
from .errors import HiBobApiError, format_exception
from .forms import (
    FORM_INSTRUCTIONS,
    MAX_OPTIONS_PER_LIST,
    REQUIRED_FIELDS,
    build_form_section,
    collect_list_ids,
    count_list_items,
    index_named_lists,
    narrow_position_lists,
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
VERIFY_BUDGET_FIELDS = (
    "/positionBudget/id",
    "/positionBudget/positionId",
    "/positionBudget/currency",
    "/positionBudget/salaryPayPeriod",
    "/positionBudget/expectedBaseSalaryCurrencyValue",
    "/positionBudget/totalPositionCostCurrencyValue",
    "/positionBudget/variablePayPeriod",
    "/positionBudget/expectedVariablePayCurrencyValue",
)
MAX_SEARCH_FIELDS = 50

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


def _normalize_position_ids(position_ids: list[str | int]) -> list[str]:
    """Deduplicate the requested position IDs, rejecting blanks."""
    normalized: list[str] = []
    for raw in position_ids:
        value = normalize_id(raw)
        if not value:
            raise ValueError("Position IDs cannot be empty.")
        if value not in normalized:
            normalized.append(value)
    return normalized


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


def _finish_write(result: dict[str, Any], problems: list[str]) -> dict[str, Any]:
    result["verified"] = not problems
    if problems:
        result["verification_error"] = "; ".join(problems)
    return result


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


def _paged_search_result(payload: Any, entries_key: str) -> dict[str, Any]:
    """Shape a cursor-paginated search response for the caller."""
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

    flattened = flatten_search_entries(entries)
    result: dict[str, Any] = {"count": len(flattened), "entries": flattened}
    if next_cursor:
        result["next_cursor"] = next_cursor
        result["has_more"] = True
    else:
        result["has_more"] = False
    return result


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
            body = _search_body(
                fields, filters or [MATCH_ALL_POSITIONS_FILTER], include_human_readable
            )
            payload = await client().search(POSITION_SEARCH_PATH, body)
            entries = payload
            if isinstance(payload, dict):
                entries = payload.get("positionEntries") or payload.get("entries")
            flattened = flatten_search_entries(entries)
            return _dump({"count": len(flattened), "entries": flattened})
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
            Field(
                description=(
                    "One or more position IDs, as returned in '/position/id'."
                ),
                min_length=1,
            ),
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
        in HiBob. Pass several position IDs at once to pay for that scan only
        once; a status filter is applied by HiBob and shortens it.

        Args:
            position_ids: Position IDs to look up, from '/position/id'.
            fields: Opening field IDs to return; the join fields are always
                added.
            statuses: Restrict to these opening statuses.
            include_human_readable: Include display labels alongside raw values.

        Returns:
            str: JSON {"count": int, "entries": [{"values": {...}, "display":
            {...}}], "counts_by_position": {"<position id>": int},
            "openings_scanned": int, "scan_complete": bool}, or an error
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
            wanted = _normalize_position_ids(position_ids)
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
            int, Field(description="Maximum entries per page.", ge=1, le=100)
        ] = 100,
        cursor: Annotated[
            str | None, Field(description="Cursor from a previous page.")
        ] = None,
        include_human_readable: Annotated[
            bool, Field(description="Also return display labels for each value.")
        ] = True,
    ) -> str:
        """Search position budgets: planned salary and total cost per position.

        Use this for cost roll-ups across planned headcount, such as the total
        budgeted cost of every vacant position in a department.

        Args:
            fields: Field IDs to return (1-50).
            filters: Optional filter clauses.
            limit: Page size, 1-100.
            cursor: Cursor from a previous page, or None to start.
            include_human_readable: Include display labels.

        Returns:
            str: JSON of the form {"count": int, "entries": [...],
            "has_more": bool, "next_cursor": str}.

        Rate limit: 100 requests/minute.
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
            return _dump(_paged_search_result(payload, "positionBudgetEntries"))
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
            str, Field(description="ID of the position to update.", min_length=1)
        ],
        fields: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Fields to change, as a flat mapping. Updatable: name, "
                    "effectiveDate, managerPositionId, positionType, fte, "
                    "employmentType, department, site, jobProfile, reason."
                )
            ),
        ],
    ) -> str:
        """Change details of an existing position.

        Only the fields supplied are modified. Use hibob_search_positions to
        find the position ID first.

        Args:
            position_id: The position's ID.
            fields: Flat mapping of field IDs to new values.

        Returns:
            str: JSON {"status": "updated", "verified": bool, "position": {...}}
            with the position read back from HiBob, including the fields just
            changed; "verification_error" explains a failed read-back, which
            does not mean the update failed. Or an error message beginning
            with "Error:".

        Examples:
            - "Move that position's start to October" -> fields with
              '/position/effectiveDate'.

        Rate limit: 10 requests/minute.
        """
        try:
            if not fields:
                raise ValueError("Provide at least one field to update.")
            validate_allowed_keys(
                OBJECT_TYPE_POSITION, fields, UPDATABLE_POSITION_FIELDS
            )
            body = build_items_envelope(OBJECT_TYPE_POSITION, fields)
            path = f"{POSITIONS_PATH}/{position_id}"
            api = client()
            patched = await api.patch(path, body)
            result: dict[str, Any] = {"status": "updated"}
            if isinstance(patched, dict) and patched:
                result["response"] = patched
            record, problem = await _verify(
                f"position {position_id}",
                _read_back(
                    api,
                    POSITION_SEARCH_PATH,
                    "/position/id",
                    position_id,
                    _read_back_fields(
                        OBJECT_TYPE_POSITION, VERIFY_POSITION_FIELDS, fields
                    ),
                    paginated=False,
                ),
            )
            if record is not None:
                result["position"] = record
            return _dump(_finish_write(result, [problem] if problem else []))
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
            str, Field(description="ID of the position to cancel.", min_length=1)
        ],
    ) -> str:
        """Cancel a planned position, removing it from the workforce plan.

        HiBob refuses to cancel a position that is currently filled; check
        '/position/status' with hibob_search_positions first. Cancelling cannot
        be undone through this API, so confirm the position ID before calling.

        Args:
            position_id: The position's ID.

        Returns:
            str: JSON confirming the cancellation, or an error message
            beginning with "Error:".

        Rate limit: 10 requests/minute.
        """
        try:
            path = f"{POSITIONS_PATH}/{position_id}/cancel"
            result = await client().patch(path)
            return _dump(result or {"status": "cancelled", "positionId": position_id})
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
            str,
            Field(description="Position the opening belongs to.", min_length=1),
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
            position_id: The parent position's ID.
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
            path = f"{POSITIONS_PATH}/{position_id}/position-openings"
            api = client()
            created = await api.post(path, body)
            result: dict[str, Any] = (
                dict(created) if isinstance(created, dict) else {"response": created}
            )
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
                    parent = normalize_id(
                        record["values"].get(OPENING_POSITION_ID_FIELD)
                    )
                    if parent != normalize_id(position_id):
                        problems.append(
                            f"opening {opening_id} belongs to position {parent}, "
                            f"not {position_id}"
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
            str, Field(description="Parent position ID.", min_length=1)
        ],
        opening_id: Annotated[
            str, Field(description="ID of the opening to update.", min_length=1)
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

        Args:
            position_id: Parent position ID.
            opening_id: The opening's ID.
            fields: Flat mapping of field IDs to new values.

        Returns:
            str: JSON confirming the update, or an error message beginning with
            "Error:".

        Rate limit: 10 requests/minute.
        """
        try:
            if not fields:
                raise ValueError("Provide at least one field to update.")
            body = build_items_envelope(OBJECT_TYPE_OPENING, fields)
            path = f"{POSITIONS_PATH}/{position_id}/position-openings/{opening_id}"
            api = client()
            patched = await api.patch(path, body)
            result: dict[str, Any] = {"status": "updated"}
            if isinstance(patched, dict) and patched:
                result["response"] = patched
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
            if record is not None:
                result["opening"] = record
            return _dump(_finish_write(result, [problem] if problem else []))
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
            str, Field(description="Parent position ID.", min_length=1)
        ],
        opening_id: Annotated[
            str, Field(description="ID of the opening to delete.", min_length=1)
        ],
    ) -> str:
        """Permanently remove an opening from a position.

        This deletes the vacancy record in HiBob and cannot be undone through
        this API. Confirm the opening ID with hibob_search_position_openings
        before calling.

        Args:
            position_id: Parent position ID.
            opening_id: The opening's ID.

        Returns:
            str: JSON confirming the deletion, or an error message beginning
            with "Error:".

        Rate limit: 10 requests/minute.
        """
        try:
            path = f"{POSITIONS_PATH}/{position_id}/position-openings/{opening_id}"
            result = await client().delete(path)
            return _dump(
                result
                or {
                    "status": "deleted",
                    "positionId": position_id,
                    "positionOpeningId": opening_id,
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
            str, Field(description="Position the budget belongs to.", min_length=1)
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

        Args:
            position_id: The position's ID.
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
            path = f"{POSITIONS_PATH}/{position_id}/position-budget"
            api = client()
            created = await api.post(path, body)
            result: dict[str, Any] = (
                dict(created) if isinstance(created, dict) else {"response": created}
            )
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
            str, Field(description="Parent position ID.", min_length=1)
        ],
        budget_id: Annotated[
            str, Field(description="ID of the budget to update.", min_length=1)
        ],
        fields: Annotated[
            dict[str, Any],
            Field(description="Budget fields to change, as a flat mapping."),
        ],
    ) -> str:
        """Change an existing position budget.

        Use hibob_search_position_budgets to find the budget ID.

        Args:
            position_id: Parent position ID.
            budget_id: The budget's ID.
            fields: Flat mapping of field IDs to new values.

        Returns:
            str: JSON confirming the update, or an error message beginning with
            "Error:".

        Rate limit: 10 requests/minute.
        """
        try:
            if not fields:
                raise ValueError("Provide at least one field to update.")
            body = build_items_envelope(OBJECT_TYPE_BUDGET, fields)
            path = f"{POSITIONS_PATH}/{position_id}/position-budget/{budget_id}"
            api = client()
            patched = await api.patch(path, body)
            result: dict[str, Any] = {"status": "updated"}
            if isinstance(patched, dict) and patched:
                result["response"] = patched
            record, problem = await _verify(
                f"budget {budget_id}",
                _read_back(
                    api,
                    BUDGET_SEARCH_PATH,
                    "/positionBudget/id",
                    budget_id,
                    _read_back_fields(OBJECT_TYPE_BUDGET, VERIFY_BUDGET_FIELDS, fields),
                    paginated=True,
                ),
            )
            if record is not None:
                result["budget"] = record
            return _dump(_finish_write(result, [problem] if problem else []))
        except Exception as exc:
            return format_exception(exc)
