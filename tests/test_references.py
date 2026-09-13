"""Position and opening references: names or IDs, and the cross-checks between them."""

from __future__ import annotations

from typing import Any

import pytest

from hibob_advanced_mcp.references import (
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


def _position_row(
    position_id: int, name: str, budget: int | None = None
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "/position/id": {"value": position_id, "humanReadable": str(position_id)},
        "/position/name": {"value": name, "humanReadable": name},
    }
    if budget is not None:
        row["/position/budget"] = {"value": budget, "humanReadable": str(budget)}
    return row


def _opening_row(opening_id: int, name: str, position_id: int) -> dict[str, Any]:
    return {
        "/positionOpening/id": {"value": opening_id},
        "/positionOpening/positionOpeningName": {"value": name},
        "/positionOpening/positionId": {"value": position_id},
    }


# ------------------------------------------------------------ classifying


def test_digits_are_an_id_and_anything_else_is_a_name() -> None:
    assert is_numeric_id("2251800820033996")
    assert is_numeric_id(" 77 ")
    assert is_numeric_id(77)
    assert not is_numeric_id("P-0000000368")
    assert not is_numeric_id("O-6853240227")
    assert not is_numeric_id("")
    assert not is_numeric_id(None)


def test_filter_uses_the_id_field_for_digits_and_the_name_field_otherwise() -> None:
    assert reference_filter(
        " 77 ", id_field="/position/id", name_field="/position/name"
    ) == {"fieldId": "/position/id", "operator": "equals", "values": ["77"]}
    assert reference_filter(
        " P-0000000368 ", id_field="/position/id", name_field="/position/name"
    ) == {
        "fieldId": "/position/name",
        "operator": "equals",
        "values": ["P-0000000368"],
    }


def test_filter_refuses_a_blank_reference() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        reference_filter("  ", id_field="/position/id", name_field="/position/name")


def test_split_references_keeps_ids_and_names_apart_and_deduplicates() -> None:
    assert split_references([7, "9", " 7 ", "P-0000000368", "p-0000000368"]) == (
        ["7", "9"],
        ["P-0000000368", "p-0000000368"],
    )


def test_split_references_refuses_a_blank() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        split_references(["7", ""])


# ---------------------------------------------------------- single match


def test_single_match_returns_the_only_row() -> None:
    row = _position_row(1, "P-1")
    assert single_match([row], "position", "P-1", id_field="/position/id") is row


def test_single_match_says_when_a_name_matches_nothing() -> None:
    with pytest.raises(ValueError, match="No position named 'P-9'"):
        single_match([], "position", "P-9", id_field="/position/id")


def test_single_match_says_when_an_id_matches_nothing() -> None:
    with pytest.raises(ValueError, match="No opening with ID '99'"):
        single_match([], "opening", "99", id_field="/positionOpening/id")


def test_single_match_refuses_to_pick_between_several() -> None:
    rows = [_position_row(1, "P-1"), _position_row(2, "P-1")]
    with pytest.raises(ValueError, match=r"'P-1' names 2 positions \(IDs 1, 2\)"):
        single_match(rows, "position", "P-1", id_field="/position/id")


# ----------------------------------------------------------------- refs


def test_position_ref_reads_id_name_and_budget_from_a_row() -> None:
    ref = PositionRef.from_row(_position_row(2251800820033996, "P-0000000368", 5))
    assert ref == PositionRef(id="2251800820033996", name="P-0000000368", budget_id="5")
    assert ref.describe() == "position P-0000000368 (ID 2251800820033996)"


def test_position_ref_without_a_name_describes_itself_by_id() -> None:
    assert PositionRef(id="77", name=None, budget_id=None).describe() == "position 77"


def test_opening_ref_reads_id_name_and_parent_from_a_row() -> None:
    ref = OpeningRef.from_row(_opening_row(8, "O-6853240227", 3))
    assert ref == OpeningRef(id="8", name="O-6853240227", position_id="3")
    assert ref.describe() == "opening O-6853240227 (ID 8)"


# ---------------------------------------------------------- cross-checks


def test_opening_under_its_own_position_passes() -> None:
    opening = OpeningRef(id="8", name="O-1", position_id="3")
    check_opening_parent(opening, PositionRef(id="3", name=None, budget_id=None))


def test_opening_under_another_position_is_refused_before_any_write() -> None:
    opening = OpeningRef(id="8", name="O-6853240227", position_id="3")
    wrong = PositionRef(id="2251800820165939", name="P-0000000910", budget_id=None)
    with pytest.raises(ValueError) as excinfo:
        check_opening_parent(opening, wrong)
    message = str(excinfo.value)
    assert "opening O-6853240227 (ID 8) belongs to position 3" in message
    assert "not position P-0000000910 (ID 2251800820165939)" in message
    assert "Nothing was written" in message


def test_budget_is_taken_from_the_position_when_none_is_given() -> None:
    position = PositionRef(id="3", name="P-3", budget_id="30")
    assert budget_to_write(position, None) == "30"
    assert budget_to_write(position, " 30 ") == "30"


def test_a_budget_belonging_to_another_position_is_refused() -> None:
    position = PositionRef(id="3", name="P-3", budget_id="30")
    with pytest.raises(ValueError) as excinfo:
        budget_to_write(position, "40")
    message = str(excinfo.value)
    assert "Budget 40 does not belong to position P-3 (ID 3)" in message
    assert "whose budget is 30" in message
    assert "Nothing was written" in message


def test_a_position_without_a_budget_cannot_have_one_updated() -> None:
    position = PositionRef(id="3", name="P-3", budget_id=None)
    with pytest.raises(ValueError, match="has no budget to update"):
        budget_to_write(position, None)


def test_a_second_budget_is_refused_before_any_write() -> None:
    with pytest.raises(ValueError, match="already has budget 30"):
        refuse_existing_budget(PositionRef(id="3", name="P-3", budget_id="30"))
    refuse_existing_budget(PositionRef(id="3", name="P-3", budget_id=None))
