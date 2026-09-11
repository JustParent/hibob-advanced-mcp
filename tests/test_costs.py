"""Joining positions to their budgets, and rolling the cost up.

Rows here mirror HiBob's live wire shape: every cell is wrapped as
``{"value": ...}``, and a money cell wraps twice, as
``{"value": {"value": 1000, "currency": "EUR"}}``.
"""

from __future__ import annotations

from hibob_advanced_mcp.costs import join_positions_to_budgets, summarize_costs


def _position(position_id: int, budget_ref: int | None, **cells: object) -> dict:
    row: dict = {"/position/id": {"value": position_id}}
    if budget_ref is not None:
        row["/position/budget"] = {"value": budget_ref}
    row.update({key: {"value": value} for key, value in cells.items()})
    return row


def _budget(budget_id: int, **cells: object) -> dict:
    return {
        "/positionBudget/id": {"value": budget_id},
        **{key: {"value": value} for key, value in cells.items()},
    }


def test_join_merges_each_position_with_the_budget_it_references() -> None:
    positions = [_position(11, 10, **{"/position/name": "P-001"})]
    budgets = [_budget(10, **{"/positionBudget/currency": "EUR"})]

    merged, unbudgeted = join_positions_to_budgets(positions, budgets)

    assert unbudgeted == []
    assert merged[0]["/position/name"] == {"value": "P-001"}
    assert merged[0]["/positionBudget/currency"] == {"value": "EUR"}


def test_join_reports_a_position_whose_budget_is_missing() -> None:
    """A position with no cost must surface, not vanish from the roll-up."""
    positions = [
        _position(11, 10),
        _position(12, None),
        _position(13, 999),  # references a budget that was not returned
    ]
    budgets = [_budget(10)]

    merged, unbudgeted = join_positions_to_budgets(positions, budgets)

    assert [_value_of(row) for row in merged] == [11]
    assert unbudgeted == ["12", "13"]


def _value_of(row: dict) -> object:
    return row["/position/id"]["value"]


def _money(amount: float, currency: str) -> dict:
    return {"value": amount, "currency": currency}


def _costed(position_id: int, converted: float, **cells: object) -> dict:
    """One merged row carrying a converted total, as the join produces."""
    return {
        "/position/id": {"value": position_id},
        "/positionBudget/convertedTotalCostCurrencyValue": {
            "value": _money(converted, "EUR")
        },
        **{key: {"value": value} for key, value in cells.items()},
    }


def test_summarize_totals_the_converted_cost() -> None:
    rows = [_costed(1, 100.50), _costed(2, 200.25)]

    summary = summarize_costs(rows)

    assert summary["currency"] == "EUR"
    assert summary["total_converted_cost"] == 300.75
    assert summary["position_count"] == 2


def test_summarize_refuses_one_total_when_converted_currencies_differ() -> None:
    """Converted costs should share the company's reporting currency.

    If a tenant ever returns more than one, a single sum would be a
    meaningless number, so the totals are reported per currency instead.
    """
    rows = [
        _costed(1, 100.0),
        {
            "/position/id": {"value": 2},
            "/positionBudget/convertedTotalCostCurrencyValue": {
                "value": _money(50.0, "USD")
            },
        },
    ]

    summary = summarize_costs(rows)

    assert summary["total_converted_cost"] is None
    assert summary["totals_by_currency"] == {"EUR": 100.0, "USD": 50.0}
    assert "currenc" in summary["warning"].lower()


def test_summarize_groups_by_department_using_its_label() -> None:
    """Departments come back as numeric IDs with the name alongside."""
    rows = [
        {
            **_costed(1, 100.0),
            "/position/department": {"value": 2649, "humanReadable": "Engineering"},
        },
        {
            **_costed(2, 50.0),
            "/position/department": {"value": 2649, "humanReadable": "Engineering"},
        },
        {
            **_costed(3, 25.0),
            "/position/department": {"value": 2650, "humanReadable": "Finance"},
        },
    ]

    summary = summarize_costs(rows, group_by="department")

    assert summary["total_converted_cost"] == 175.0
    assert summary["groups"] == [
        {"group": "Engineering", "position_count": 2, "total_converted_cost": 150.0},
        {"group": "Finance", "position_count": 1, "total_converted_cost": 25.0},
    ]


def test_summarize_rejects_a_dimension_hibob_has_no_field_for() -> None:
    rows = [_costed(1, 100.0)]

    try:
        summarize_costs(rows, group_by="manager")
    except ValueError as exc:
        assert "manager" in str(exc)
        assert "department" in str(exc)  # names what can be grouped by
    else:
        raise AssertionError("expected a ValueError naming the valid dimensions")
