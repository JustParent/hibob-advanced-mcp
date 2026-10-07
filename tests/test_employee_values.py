"""Coercing given values by field type, and building and checking writes."""

from __future__ import annotations

import pytest

from hibob_advanced_mcp.employee_rows import put_body, same_value
from hibob_advanced_mcp.employee_values import NeedsInput, coerce_value
from hibob_advanced_mcp.people_fields import normalize_people_fields
from people_data import FIELDS

BY_ID = {field.id: field for field in normalize_people_fields(FIELDS)}


@pytest.mark.parametrize(
    ("field_id", "given", "expected"),
    [
        ("work.startDate", " 2026-11-01 ", "2026-11-01"),
        ("about.custom.field_800", "12", 12),
        ("about.custom.field_800", "12.5", 12.5),
        ("about.custom.field_800", 7, 7),
        ("about.custom.field_700", True, True),
        ("about.custom.field_700", "No", False),
        ("home.mobilePhone", " 07700 900123 ", "07700 900123"),
        (
            "financial.custom.field_500",
            {"value": "5000", "currency": "gbp"},
            {"value": 5000, "currency": "GBP"},
        ),
    ],
)
def test_coerce_value(field_id: str, given, expected) -> None:
    assert coerce_value(BY_ID[field_id], given) == expected


@pytest.mark.parametrize(
    ("field_id", "given"),
    [
        ("work.startDate", "01/11/2026"),
        ("work.startDate", "2026-02-30"),
        ("about.custom.field_800", "1,000"),
        ("about.custom.field_700", "maybe"),
        ("home.mobilePhone", ["a"]),
        ("home.mobilePhone", None),
        ("financial.custom.field_500", {"value": 1, "currency": "pounds"}),
    ],
)
def test_coerce_value_refuses(field_id: str, given) -> None:
    with pytest.raises(ValueError):
        coerce_value(BY_ID[field_id], given)


def test_a_bare_amount_asks_for_its_currency() -> None:
    with pytest.raises(NeedsInput) as excinfo:
        coerce_value(BY_ID["financial.custom.field_500"], 5000)
    assert "currency" in excinfo.value.question["question"]
    assert excinfo.value.question["argument"] == "changes"


def test_put_body_nests_by_path_with_root_fields_at_the_top() -> None:
    assert put_body(
        {
            "root.firstName": "Janet",
            "home.mobilePhone": "1",
            "work.custom.field_100": "M",
            "about.custom.field_300": ["1"],
        }
    ) == {
        "firstName": "Janet",
        "home": {"mobilePhone": "1"},
        "work": {"custom": {"field_100": "M"}},
        "about": {"custom": {"field_300": ["1"]}},
    }


@pytest.mark.parametrize(
    ("sent", "read", "same"),
    [
        ("M", "M", True),
        (12, 12.0, True),
        (12, "12", True),
        (
            "3332883884017713999",
            {"id": "3332883884017713999", "email": "s@x.com"},
            True,
        ),
        (["1", "2"], ["2", "1"], True),
        (
            {"value": 5000, "currency": "GBP"},
            {"value": 5000.0, "currency": "GBP"},
            True,
        ),
        ({"value": 5000, "currency": "GBP"}, {"value": 5000, "currency": "EUR"}, False),
        (True, True, True),
        (True, "true", True),
        ("M", None, False),
        ({"id": "5"}, {"id": "5", "displayName": "Sam"}, True),
        ({"id": "5"}, {"id": "9", "displayName": "Sam"}, False),
        ({"id": "5"}, None, False),
        ({"a": 1, "b": {"c": 2}}, {"a": 1.0, "b": {"c": 2}, "x": 9}, True),
        ({"a": 1, "b": {"c": 2}}, {"a": 1, "b": {"c": 3}}, False),
        ({"value": 5, "currency": "GBP"}, {"value": 5, "currency": "gbp"}, True),
        ("M", "L", False),
    ],
)
def test_same_value(sent, read, same: bool) -> None:
    assert same_value(sent, read) is same
