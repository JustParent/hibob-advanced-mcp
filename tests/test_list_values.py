"""Turning option names back into the list item IDs HiBob wants."""

from __future__ import annotations

from typing import Any

import pytest

from hibob_advanced_mcp.list_values import (
    ListField,
    find_list_field,
    resolve_list_values,
)


def _meta(
    field_id: str, name: str, field_type: str, list_id: str | None = None
) -> dict:
    descriptor: dict[str, Any] = {
        "id": field_id,
        "name": name,
        "fieldType": {"type": field_type},
    }
    if list_id:
        descriptor["fieldType"]["typeData"] = {"listId": list_id}
    return descriptor


METADATA = [
    _meta("/position/site", "Site", "list_id", "site"),
    _meta(
        "/position/field_24133483",
        "Locations for hiring",
        "multi_list",
        "entity_multi_list_1",
    ),
    _meta("/position/field_24285464", "Hiring Manager email", "text"),
    _meta("/position/department", "Function", "list", "department"),
]

LOCATIONS = [
    {"id": "267873082", "name": "Amsterdam", "value": "Amsterdam"},
    {"id": "267873068", "name": "Lisbon", "value": "Lisbon"},
    {"id": "267873081", "name": "Madrid", "value": "Madrid"},
    {"id": "267873079", "name": "Spain", "value": "Spain"},
]

SITES_TREE = [
    {
        "id": 1,
        "name": "Germany",
        "children": [
            {"id": 11, "name": "Berlin - Office"},
            {"id": 12, "name": "Berlin - Remote"},
        ],
    },
    {"id": 2, "name": "Spain", "children": [{"id": 21, "name": "Madrid - Office"}]},
]


# ------------------------------------------------------------ finding the field


def test_field_is_found_by_id() -> None:
    assert find_list_field(
        "position", METADATA, "/position/field_24133483"
    ) == ListField(
        field_id="/position/field_24133483",
        name="Locations for hiring",
        type="multi_list",
        list_id="entity_multi_list_1",
    )


def test_field_is_found_by_bare_id_and_by_label_in_any_case() -> None:
    by_bare = find_list_field("position", METADATA, "field_24133483")
    by_label = find_list_field("position", METADATA, "  locations FOR hiring ")
    assert by_bare.list_id == by_label.list_id == "entity_multi_list_1"


def test_a_field_that_is_not_list_backed_is_refused() -> None:
    with pytest.raises(ValueError, match="'Hiring Manager email' is a text field"):
        find_list_field("position", METADATA, "Hiring Manager email")


def test_an_unknown_field_names_the_list_backed_fields() -> None:
    with pytest.raises(ValueError) as excinfo:
        find_list_field("position", METADATA, "Locations")
    message = str(excinfo.value)
    assert "No position field called 'Locations'" in message
    assert "Locations for hiring" in message and "Site" in message
    assert "Hiring Manager email" not in message


# ------------------------------------------------------------ resolving values


def test_names_resolve_to_ids_in_the_order_given() -> None:
    result = resolve_list_values(LOCATIONS, ["Madrid", "Lisbon"])
    assert result["resolved"] == {"Madrid": "267873081", "Lisbon": "267873068"}
    assert result["values"] == ["267873081", "267873068"]
    assert result["complete"] is True
    assert result["unmatched"] == [] and result["ambiguous"] == []


def test_matching_ignores_case_and_surrounding_space() -> None:
    result = resolve_list_values(LOCATIONS, [" madrid ", "LISBON"])
    assert result["values"] == ["267873081", "267873068"]


def test_an_id_passed_as_a_value_resolves_to_itself() -> None:
    result = resolve_list_values(LOCATIONS, ["267873079", "Madrid"])
    assert result["resolved"] == {"267873079": "267873079", "Madrid": "267873081"}


def test_repeated_names_are_resolved_once() -> None:
    result = resolve_list_values(LOCATIONS, ["Madrid", "madrid", "Madrid"])
    assert result["values"] == ["267873081"]


def test_a_tree_leaf_matches_by_its_own_name_or_its_path() -> None:
    result = resolve_list_values(
        SITES_TREE, ["Berlin - Office", "Spain > Madrid - Office"]
    )
    assert result["values"] == ["11", "21"]


def test_a_branch_of_a_tree_is_not_submittable_and_offers_its_leaves() -> None:
    result = resolve_list_values(SITES_TREE, ["Germany"])
    assert result["complete"] is False
    assert result["values"] == []
    [miss] = result["unmatched"]
    assert miss["name"] == "Germany"
    assert [c["id"] for c in miss["candidates"]] == ["11", "12"]


def test_an_unknown_name_comes_back_with_nearest_candidates() -> None:
    result = resolve_list_values(LOCATIONS, ["Madrid", "Lisboa"])
    assert result["complete"] is False
    assert result["values"] == ["267873081"]
    [miss] = result["unmatched"]
    assert miss["name"] == "Lisboa"
    assert all({"id", "name"} <= set(c) for c in miss["candidates"])


def test_a_name_shared_by_several_items_is_ambiguous_not_guessed() -> None:
    items = [*LOCATIONS, {"id": "999", "name": "Madrid", "value": "Madrid (old)"}]
    result = resolve_list_values(items, ["Madrid"])
    assert result["complete"] is False
    assert result["values"] == []
    [clash] = result["ambiguous"]
    assert clash["name"] == "Madrid"
    assert {c["id"] for c in clash["candidates"]} == {"267873081", "999"}


def test_blank_values_are_refused() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        resolve_list_values(LOCATIONS, ["Madrid", "  "])
