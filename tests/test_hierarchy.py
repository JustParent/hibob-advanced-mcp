"""Position hierarchy: one scan of every position, walked in memory."""

from __future__ import annotations

from typing import Any

from hibob_advanced_mcp.hierarchy import (
    positions_under,
    resolve_root,
    shape_position,
)


def _row(
    position_id: int,
    name: str,
    manager: int | None = None,
    holder: str | None = None,
    holder_id: int | None = None,
    status: str = "filled",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "/position/id": {"value": position_id, "humanReadable": str(position_id)},
        "/position/name": {"value": name, "humanReadable": name},
        "/position/status": {"value": status, "humanReadable": status.title()},
        "/position/department": {"value": "1", "humanReadable": "Data"},
        "/position/jobProfile": {"value": 5, "humanReadable": "Engineer"},
        "/position/site": {"value": 9, "humanReadable": "London"},
        "/position/hasOpenRequests": {"value": False, "humanReadable": "No"},
    }
    if manager is not None:
        row["/position/managerPositionId"] = {
            "value": manager,
            "humanReadable": f"Data \\ Engineer \\ P-{manager}",
        }
    if holder_id is not None:
        row["/position/filledBy"] = {"value": str(holder_id), "humanReadable": holder}
    return row


ROWS = [
    _row(1, "P-1", holder="Jane Doe", holder_id=100),
    _row(2, "P-2", manager=1, holder="Sam Roe", holder_id=200),
    _row(3, "P-3", manager=1, holder="Jane Poe", holder_id=300, status="starting"),
    _row(4, "P-4", manager=2, status="vacant"),
    _row(5, "P-5", holder="Alex Foo", holder_id=500),
]
POSITIONS = [shape_position(row) for row in ROWS]


def test_shape_position_keeps_ids_and_labels_apart() -> None:
    assert shape_position(ROWS[1]) == {
        "id": "2",
        "name": "P-2",
        "status": "filled",
        "holder": "Sam Roe",
        "holder_id": "200",
        "department": "Data",
        "job_profile": "Engineer",
        "site": "London",
        "manager_position_id": "1",
        "has_open_requests": False,
    }
    assert shape_position(ROWS[3])["holder"] is None
    assert shape_position(ROWS[0])["manager_position_id"] is None


def test_resolve_root_tries_id_name_employee_id_then_holder_words() -> None:
    assert resolve_root(POSITIONS, "2") == ([POSITIONS[1]], "position_id")
    assert resolve_root(POSITIONS, "p-3") == ([POSITIONS[2]], "position_name")
    assert resolve_root(POSITIONS, "200") == ([POSITIONS[1]], "employee_id")
    assert resolve_root(POSITIONS, "doe jane") == ([POSITIONS[0]], "holder_name")
    assert resolve_root(POSITIONS, "jane") == (
        [POSITIONS[0], POSITIONS[2]],
        "holder_name",
    )
    assert resolve_root(POSITIONS, "nobody") == ([], "holder_name")
    assert resolve_root(POSITIONS, "  ") == ([], "position_id")


def test_resolve_root_marks_a_name_no_holder_has_in_full_as_partial() -> None:
    """Sharing a first name is not being the person asked for: a query no
    holder matches in full is marked partial so it is not taken as found."""
    assert resolve_root(POSITIONS, "sam smith") == (
        [POSITIONS[1]],
        "partial_holder_name",
    )
    assert resolve_root(POSITIONS, "jane smith") == (
        [POSITIONS[0], POSITIONS[2]],
        "partial_holder_name",
    )
    assert resolve_root(POSITIONS, "sa roe") == ([POSITIONS[1]], "holder_name")


def test_positions_under_walks_depth_first_with_depths() -> None:
    assert [(p["id"], p["depth"]) for p in positions_under(POSITIONS, "1")] == [
        ("2", 1),
        ("4", 2),
        ("3", 1),
    ]
    assert [p["id"] for p in positions_under(POSITIONS, "1", depth=1)] == ["2", "3"]
    assert positions_under(POSITIONS, "5") == []


def test_positions_under_survives_a_cycle() -> None:
    looped = [
        shape_position(_row(1, "P-1", manager=2)),
        shape_position(_row(2, "P-2", manager=1)),
    ]
    assert [p["id"] for p in positions_under(looped, "1")] == ["2"]
