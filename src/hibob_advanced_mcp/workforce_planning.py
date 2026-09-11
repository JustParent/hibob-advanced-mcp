"""HiBob Workforce Planning tools: positions, position openings and budgets.

Tools are registered through :func:`register_workforce_planning_tools` so that
write tools can be withheld in read-only deployments, and so further HiBob
domains can be added as sibling modules.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from .client import HiBobClient, get_client
from .envelopes import (
    OBJECT_TYPE_BUDGET,
    OBJECT_TYPE_OPENING,
    OBJECT_TYPE_POSITION,
    build_items_envelope,
    flatten_search_entries,
    validate_allowed_keys,
    validate_required_keys,
)
from .errors import HiBobApiError, format_exception
from .forms import (
    FORM_INSTRUCTIONS,
    REQUIRED_FIELDS,
    build_form_section,
    collect_list_ids,
    index_named_lists,
    lookup_named_list,
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
OpeningStatusLiteral = Literal["vacant", "starting", "filled", "departing"]

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
# an empty filter list, the scan sends a clause every opening satisfies: an
# ID HiBob never assigns, so no real opening is excluded.
IMPOSSIBLE_OPENING_ID = "0"
OPENING_SCAN_PAGE_SIZE = 100
# Bounds a scan at 10,000 openings, so a paging fault cannot loop forever.
MAX_OPENING_SCAN_PAGES = 100

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
    field_id=OPENING_ID_FIELD, operator="notEqual", values=[IMPOSSIBLE_OPENING_ID]
)


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


def _normalize_id(value: Any) -> str:
    """Render an ID the way HiBob's JSON does, so ints and strings compare."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _normalize_position_ids(position_ids: list[str | int]) -> list[str]:
    """Deduplicate the requested position IDs, rejecting blanks."""
    normalized: list[str] = []
    for raw in position_ids:
        value = _normalize_id(raw)
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
        key = _normalize_id(values.get(OPENING_POSITION_ID_FIELD))
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


async def _resolve_named_lists(
    client: HiBobClient, list_ids: set[str], include_archived: bool
) -> tuple[dict[str, list[Any]], list[str]]:
    """Fetch the named lists behind ``list_ids``, as an index by list name.

    One call fetches every list; an ID missing from that response is then
    fetched by name, in case the metadata's list IDs do not match the names in
    the combined response. Failures become warnings rather than errors, since
    a form without drop-down options is still worth returning.
    """
    suffix = "?includeArchived=true" if include_archived else ""
    warnings: list[str] = []
    index: dict[str, list[Any]] = {}
    try:
        index = index_named_lists(await client.get(f"{NAMED_LISTS_PATH}{suffix}"))
    except HiBobApiError as exc:
        warnings.append(f"Could not fetch the company named lists: {exc}")
    for list_id in sorted(list_ids):
        if lookup_named_list(index, list_id) is not None:
            continue
        try:
            payload = await client.get(f"{NAMED_LISTS_PATH}/{list_id}{suffix}")
        except HiBobApiError as exc:
            warnings.append(
                f"List {list_id!r} could not be fetched, so its field has no "
                f"options: {exc}"
            )
            continue
        items = payload.get("items") if isinstance(payload, dict) else payload
        index[list_id] = items if isinstance(items, list) else []
    return index, warnings


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
    else:
        entries = payload.get(entries_key) if isinstance(payload, dict) else None
        metadata = (
            payload.get("response_metadata") if isinstance(payload, dict) else None
        )
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
) -> None:
    """Register workforce planning tools on ``mcp``.

    When ``read_only`` is true only the read tools are registered, so a
    deployment can expose planning data without any ability to change it.
    """

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

        Use this before hibob_create_position, hibob_create_position_opening
        or hibob_create_position_budget. It replaces a chain of
        hibob_list_workforce_fields and hibob_get_company_named_lists calls.

        Args:
            object_type: 'position', 'positionOpening' or 'positionBudget'.
            include_archived_list_items: Include archived list items.

        Returns:
            str: JSON {"form": str, "submit_with": str, "instructions": [str],
            "sections": [{"object_type", "role", "argument", "required_fields",
            "fields": [{"id", "name", "type", "required", "options" or
            "allowed_values", ...}], "read_only_fields": [...]}], "warnings":
            [str]}, or an error message beginning with "Error:".

        Examples:
            - "I want to plan a new engineering position" ->
              object_type='position'
            - "Add another opening to position 4821" ->
              object_type='positionOpening'

        Rate limit: metadata 50 requests/minute; one metadata request per
        section plus one named-lists request.
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
                    api, list_ids, include_archived_list_items
                )
            sections = [
                build_form_section(
                    section_type, payload, named_lists, role=role, argument=argument
                )
                for (section_type, role, argument), payload in zip(
                    layout, metadata, strict=True
                )
            ]
            result: dict[str, Any] = {
                "form": object_type,
                "submit_with": FORM_SUBMIT_TOOLS[object_type],
                "instructions": list(FORM_INSTRUCTIONS),
                "sections": sections,
            }
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
                    "Optional single list to fetch, e.g. 'department' or 'site'. "
                    "Omit to return every named list."
                )
            ),
        ] = None,
    ) -> str:
        """Look up the allowed values of HiBob's named lists.

        Position fields such as department, site and employment type must be
        set to a list item from HiBob's named lists rather than to free text.
        This tool resolves those names to the IDs that hibob_create_position
        and hibob_update_position expect.

        Args:
            list_name: A single list to fetch, or None for all lists.

        Returns:
            str: JSON mapping list names to their items, each with an ID and a
            display name.

        Examples:
            - "Which departments exist?" -> list_name='department'
            - Use before hibob_create_position to turn "Engineering" into its
              list item ID.
        """
        try:
            path = NAMED_LISTS_PATH
            if list_name:
                path = f"{NAMED_LISTS_PATH}/{list_name.strip()}"
            return _dump(await client().get(path))
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
                    "'/position/name', '/position/hasOpenRequests', '/position/id'."
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
            body = _search_body(fields, filters, include_human_readable)
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
                    "'/positionOpening/status' (vacant, starting, filled, "
                    "departing), '/positionOpening/positionOpeningName'. HiBob "
                    "cannot filter by '/positionOpening/positionId'; use "
                    "hibob_get_openings_for_positions for that."
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
            body = _search_body(fields, filters, include_human_readable)
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
                    "Only return openings in these statuses (vacant, starting, "
                    "filled, departing). Omit for every opening."
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
            body = _search_body(fields, filters, include_human_readable)
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
            str: JSON {"id": int, "positionOpeningId": int} identifying the new
            position, or an error message beginning with "Error:".

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
            return _dump(await client().post(POSITIONS_PATH, body))
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
            str: JSON confirming the update, or an error message beginning with
            "Error:".

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
            return _dump(await client().patch(path, body) or {"status": "updated"})
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
            str: JSON {"id": int, "positionOpeningId": int}, or an error
            message beginning with "Error:".

        Rate limit: 10 requests/minute.
        """
        try:
            validate_required_keys(OBJECT_TYPE_OPENING, fields, REQUIRED_OPENING_FIELDS)
            body = build_items_envelope(OBJECT_TYPE_OPENING, fields)
            path = f"{POSITIONS_PATH}/{position_id}/position-openings"
            return _dump(await client().post(path, body))
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
            return _dump(await client().patch(path, body) or {"status": "updated"})
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
            str: JSON {"positionBudgetId": int}, or an error message beginning
            with "Error:".

        Examples:
            - "Budget 65k a year for that role" -> fields with
              expectedBaseSalaryCurrencyValue, salaryPayPeriod and currency.

        Rate limit: 10 requests/minute.
        """
        try:
            validate_required_keys(OBJECT_TYPE_BUDGET, fields, REQUIRED_BUDGET_FIELDS)
            body = build_items_envelope(OBJECT_TYPE_BUDGET, fields)
            path = f"{POSITIONS_PATH}/{position_id}/position-budget"
            return _dump(await client().post(path, body))
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
            return _dump(await client().patch(path, body) or {"status": "updated"})
        except Exception as exc:
            return format_exception(exc)
