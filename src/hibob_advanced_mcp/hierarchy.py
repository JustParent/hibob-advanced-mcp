"""Position hierarchy: who reports to whom, from one scan of every position.

HiBob's position search cannot filter by manager position or by holder, but
it returns every position in one unpaginated response, each with its manager
position and the employee filling it. These pure functions turn that scan
into a reporting tree: resolve the position a caller means (by ID, position
name, employee ID or holder's name) and walk the positions beneath it.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .envelopes import normalize_id
from .forms import rank_matches

POSITION_ID_FIELD = "/position/id"
POSITION_NAME_FIELD = "/position/name"
POSITION_STATUS_FIELD = "/position/status"
POSITION_FILLED_BY_FIELD = "/position/filledBy"
POSITION_DEPARTMENT_FIELD = "/position/department"
POSITION_JOB_PROFILE_FIELD = "/position/jobProfile"
POSITION_SITE_FIELD = "/position/site"
POSITION_MANAGER_FIELD = "/position/managerPositionId"
POSITION_OPEN_REQUESTS_FIELD = "/position/hasOpenRequests"

# What the hierarchy scan requests: enough to identify each position, say
# who fills it, and place it under its manager.
HIERARCHY_FIELDS: tuple[str, ...] = (
    POSITION_ID_FIELD,
    POSITION_NAME_FIELD,
    POSITION_STATUS_FIELD,
    POSITION_FILLED_BY_FIELD,
    POSITION_DEPARTMENT_FIELD,
    POSITION_JOB_PROFILE_FIELD,
    POSITION_SITE_FIELD,
    POSITION_MANAGER_FIELD,
    POSITION_OPEN_REQUESTS_FIELD,
)

RESOLVED_BY_POSITION_ID = "position_id"
RESOLVED_BY_POSITION_NAME = "position_name"
RESOLVED_BY_EMPLOYEE_ID = "employee_id"
RESOLVED_BY_HOLDER_NAME = "holder_name"
RESOLVED_BY_EMAIL = "email"


def _cell(row: dict[str, Any], field: str) -> tuple[Any, Any]:
    """A search cell's raw value and label."""
    cell = row.get(field)
    if isinstance(cell, dict) and ("value" in cell or "humanReadable" in cell):
        return cell.get("value"), cell.get("humanReadable")
    return cell, None


def _label(row: dict[str, Any], field: str) -> str | None:
    value, label = _cell(row, field)
    if label is not None:
        return str(label)
    return None if value is None else str(value)


def _id_or_none(value: Any) -> str | None:
    return None if value is None else normalize_id(value)


def shape_position(row: dict[str, Any]) -> dict[str, Any]:
    """Reduce a position search row to what a reporting tree needs.

    IDs keep HiBob's raw values, as strings so ints and strings compare.
    Department, job profile and site keep their labels, since they are read
    here rather than written back.
    """
    position_id, _ = _cell(row, POSITION_ID_FIELD)
    manager_id, _ = _cell(row, POSITION_MANAGER_FIELD)
    holder_id, holder = _cell(row, POSITION_FILLED_BY_FIELD)
    status, _ = _cell(row, POSITION_STATUS_FIELD)
    open_requests, _ = _cell(row, POSITION_OPEN_REQUESTS_FIELD)
    if holder is None and holder_id is not None:
        holder = str(holder_id)
    return {
        "id": _id_or_none(position_id),
        "name": _label(row, POSITION_NAME_FIELD),
        "status": status,
        "holder": holder,
        "holder_id": _id_or_none(holder_id),
        "department": _label(row, POSITION_DEPARTMENT_FIELD),
        "job_profile": _label(row, POSITION_JOB_PROFILE_FIELD),
        "site": _label(row, POSITION_SITE_FIELD),
        "manager_position_id": _id_or_none(manager_id),
        "has_open_requests": None if open_requests is None else bool(open_requests),
    }


def resolve_root(
    positions: list[dict[str, Any]], query: Any
) -> tuple[list[dict[str, Any]], str]:
    """The positions ``query`` picks out, and how.

    Tries a position ID, then a position name, then the holder's employee
    ID, then words of the holder's name. Several matches mean the caller
    must choose; none means nothing was found.
    """
    text = str(query or "").strip()
    if not text:
        return [], RESOLVED_BY_POSITION_ID
    by_id = [p for p in positions if p.get("id") == text]
    if by_id:
        return by_id, RESOLVED_BY_POSITION_ID
    lowered = text.lower()
    by_name = [
        p for p in positions if str(p.get("name") or "").strip().lower() == lowered
    ]
    if by_name:
        return by_name, RESOLVED_BY_POSITION_NAME
    by_employee = [p for p in positions if p.get("holder_id") == text]
    if by_employee:
        return by_employee, RESOLVED_BY_EMPLOYEE_ID
    leaves = [
        {"id": p["id"], "name": p["holder"]} for p in positions if p.get("holder")
    ]
    order = {leaf["id"]: rank for rank, leaf in enumerate(rank_matches(leaves, text))}
    matched = [p for p in positions if p.get("id") in order]
    matched.sort(key=lambda p: order[p["id"]])
    return matched, RESOLVED_BY_HOLDER_NAME


def positions_under(
    positions: list[dict[str, Any]], root_id: Any, depth: int | None = None
) -> list[dict[str, Any]]:
    """Positions beneath ``root_id``, depth first, each with its ``depth``.

    ``depth`` limits how many levels down to go (1 is direct reports). A
    position already visited is skipped, so a cycle in the data cannot loop.
    """
    children: dict[str, list[dict[str, Any]]] = {}
    for position in positions:
        manager_id = position.get("manager_position_id")
        if manager_id is not None:
            children.setdefault(manager_id, []).append(position)

    result: list[dict[str, Any]] = []
    seen = {normalize_id(root_id)}

    def walk(parent_id: str, level: int) -> None:
        if depth is not None and level > depth:
            return
        for child in children.get(parent_id, []):
            child_id = child.get("id")
            if child_id is None or child_id in seen:
                continue
            seen.add(child_id)
            result.append({**child, "depth": level})
            walk(child_id, level + 1)

    walk(normalize_id(root_id), 1)
    return result


def summarize_tree(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Shape of a subtree from :func:`positions_under`: reports and depth."""
    return {
        "direct_reports": sum(1 for entry in entries if entry.get("depth") == 1),
        "max_depth": max((int(entry.get("depth", 0)) for entry in entries), default=0),
    }


def counts_by_status(entries: list[dict[str, Any]]) -> dict[str, int]:
    """How many positions are in each status, in the order first seen."""
    return dict(Counter(str(entry.get("status")) for entry in entries))
