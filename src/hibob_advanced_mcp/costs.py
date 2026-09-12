"""Position cost: joining a position to its budget, and rolling the cost up.

Cost does not live on a position. It lives on a separate ``positionBudget``
object, and the only link between the two is ``/position/budget`` on the
position, which holds the budget's ID; a budget carries no position ID of its
own. HiBob can neither filter nor group by any cost field, so both the join
and every roll-up happen here.
"""

from __future__ import annotations

from typing import Any

from .envelopes import normalize_id

POSITION_ID_FIELD = "/position/id"
POSITION_BUDGET_REF_FIELD = "/position/budget"
BUDGET_ID_FIELD = "/positionBudget/id"


def cell_value(row: dict[str, Any], field: str) -> Any:
    """A search cell's raw value, unwrapped from HiBob's ``{"value": ...}``."""
    cell = row.get(field)
    return cell.get("value") if isinstance(cell, dict) else cell


def join_positions_to_budgets(
    position_rows: list[dict[str, Any]], budget_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Merge each position row with the budget row it references.

    Returns the merged rows and the IDs of positions whose budget was not
    found, so a position without cost is reported rather than dropped.
    """
    by_id = {
        normalize_id(cell_value(row, BUDGET_ID_FIELD)): row
        for row in budget_rows
        if cell_value(row, BUDGET_ID_FIELD) is not None
    }

    merged: list[dict[str, Any]] = []
    unbudgeted: list[str] = []
    for position in position_rows:
        ref = cell_value(position, POSITION_BUDGET_REF_FIELD)
        budget = by_id.get(normalize_id(ref)) if ref is not None else None
        if budget is None:
            unbudgeted.append(normalize_id(cell_value(position, POSITION_ID_FIELD)))
            continue
        merged.append({**position, **budget})
    return merged, unbudgeted


CONVERTED_COST_FIELD = "/positionBudget/convertedTotalCostCurrencyValue"
UNKNOWN_CURRENCY = "unknown"

# Dimensions a cost roll-up can group by. HiBob can group by none of them, so
# every one of these is applied here, over the joined rows.
GROUP_BY_FIELDS = {
    "department": "/position/department",
    "site": "/position/site",
    "status": "/position/status",
    "jobProfile": "/position/jobProfile",
    "currency": "/positionBudget/currency",
}
UNGROUPED = "(none)"


def _label(row: dict[str, Any], field: str) -> str:
    """A cell's display label, falling back to its raw value."""
    cell = row.get(field)
    if isinstance(cell, dict):
        human = cell.get("humanReadable")
        if human is not None:
            return str(human)
        value = cell.get("value")
        return UNGROUPED if value is None else str(value)
    return UNGROUPED if cell is None else str(cell)


def _money(row: dict[str, Any], field: str) -> tuple[float | None, str | None]:
    """A currency cell's amount and currency.

    Money nests twice: ``{"value": {"value": 1000, "currency": "EUR"}}``.
    """
    inner = cell_value(row, field)
    if not isinstance(inner, dict):
        return None, None
    amount = inner.get("value")
    return (
        float(amount) if isinstance(amount, (int, float)) else None,
        inner.get("currency"),
    )


def summarize_costs(
    merged_rows: list[dict[str, Any]], group_by: str | None = None
) -> dict[str, Any]:
    """Roll the converted cost up across merged position rows.

    Only the converted figures are summed. HiBob reports
    ``totalPositionCostCurrencyValue`` in each position's local currency, so
    adding those together across a company would produce a meaningless number.
    """
    totals: dict[str, float] = {}
    for row in merged_rows:
        amount, currency = _money(row, CONVERTED_COST_FIELD)
        if amount is None:
            continue
        totals[currency or UNKNOWN_CURRENCY] = (
            totals.get(currency or UNKNOWN_CURRENCY, 0.0) + amount
        )

    summary: dict[str, Any] = {"position_count": len(merged_rows)}
    if len(totals) > 1:
        summary["currency"] = None
        summary["total_converted_cost"] = None
        summary["totals_by_currency"] = {k: round(v, 2) for k, v in totals.items()}
        summary["warning"] = (
            "Converted costs came back in more than one currency "
            f"({', '.join(sorted(totals))}), so they are reported per currency "
            "rather than as a single total."
        )
        return summary

    currency, total = next(iter(totals.items()), (None, 0.0))
    summary["currency"] = currency
    summary["total_converted_cost"] = round(total, 2)
    if group_by:
        summary["groups"] = _group(merged_rows, group_by)
    return summary


def _group(merged_rows: list[dict[str, Any]], group_by: str) -> list[dict[str, Any]]:
    """Per-group counts and converted totals, largest total first."""
    field = GROUP_BY_FIELDS.get(group_by)
    if field is None:
        raise ValueError(
            f"Cannot group by {group_by!r}. Available: "
            f"{', '.join(sorted(GROUP_BY_FIELDS))}."
        )
    counts: dict[str, int] = {}
    totals: dict[str, float] = {}
    for row in merged_rows:
        key = _label(row, field)
        counts[key] = counts.get(key, 0) + 1
        amount, _ = _money(row, CONVERTED_COST_FIELD)
        totals[key] = totals.get(key, 0.0) + (amount or 0.0)
    return [
        {
            "group": key,
            "position_count": counts[key],
            "total_converted_cost": round(totals[key], 2),
        }
        for key in sorted(counts, key=lambda k: (-totals[k], k))
    ]


POSITION_NAME_FIELD = "/position/name"


def index_positions_by_budget(
    position_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Map each budget's ID to the position that references it.

    HiBob puts no position link on a budget, so the only way to say which
    position a budget belongs to is to read "/position/budget" off every
    position and invert it.
    """
    index: dict[str, dict[str, Any]] = {}
    for row in position_rows:
        ref = cell_value(row, POSITION_BUDGET_REF_FIELD)
        if ref is None:
            continue
        position_id = cell_value(row, POSITION_ID_FIELD)
        index[normalize_id(ref)] = {
            "id": None if position_id is None else normalize_id(position_id),
            "name": cell_value(row, POSITION_NAME_FIELD),
        }
    return index


def attach_positions(
    entries: list[dict[str, Any]], index: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Add the owning position to each flattened budget entry.

    The link is synthesised here, not returned by HiBob, so it sits beside
    ``values`` rather than among the field IDs: it cannot be filtered on or
    written back. A budget no position references gets ``None``, which is a
    real state and is reported rather than left off.
    """
    attached: list[dict[str, Any]] = []
    for entry in entries:
        values = entry.get("values")
        budget_id = values.get(BUDGET_ID_FIELD) if isinstance(values, dict) else None
        position = index.get(normalize_id(budget_id)) if budget_id is not None else None
        attached.append({**entry, "position": position})
    return attached
