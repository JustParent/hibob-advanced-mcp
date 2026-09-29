"""Envelope wrapping, key normalization and search flattening tests."""

from __future__ import annotations

import pytest

from hibob_advanced_mcp.envelopes import (
    OBJECT_TYPE_BUDGET,
    OBJECT_TYPE_OPENING,
    OBJECT_TYPE_POSITION,
    build_items_envelope,
    flatten_search_entries,
    flatten_search_entry,
    normalize_field_key,
    validate_allowed_keys,
    validate_required_keys,
    wrap_fields,
)


@pytest.mark.parametrize(
    "key,expected",
    [
        ("fte", "/position/fte"),
        ("position/fte", "/position/fte"),
        ("/position/fte", "/position/fte"),
        ("  /position/fte  ", "/position/fte"),
    ],
)
def test_normalize_accepts_prefixed_and_bare_keys(key: str, expected: str) -> None:
    assert normalize_field_key(OBJECT_TYPE_POSITION, key) == expected


def test_normalize_rejects_wrong_object_type() -> None:
    with pytest.raises(ValueError, match="belongs to 'positionOpening'"):
        normalize_field_key(OBJECT_TYPE_POSITION, "/positionOpening/expectedStartDate")


def test_normalize_rejects_unknown_prefix() -> None:
    with pytest.raises(ValueError, match="Unrecognized field prefix"):
        normalize_field_key(OBJECT_TYPE_POSITION, "/employee/name")


def test_normalize_rejects_empty_key() -> None:
    with pytest.raises(ValueError):
        normalize_field_key(OBJECT_TYPE_POSITION, "  ")


def test_wrap_fields_wraps_raw_values() -> None:
    assert wrap_fields(OBJECT_TYPE_POSITION, {"fte": 100}) == {
        "/position/fte": {"value": 100}
    }


def test_wrap_fields_passes_through_prewrapped_values() -> None:
    wrapped = wrap_fields(
        OBJECT_TYPE_POSITION, {"/position/fte": {"value": 50, "humanReadable": "50%"}}
    )
    assert wrapped == {"/position/fte": {"value": 50}}


def test_wrap_fields_keeps_dict_values_that_are_not_envelopes() -> None:
    wrapped = wrap_fields(OBJECT_TYPE_POSITION, {"/position/meta": {"a": 1}})
    assert wrapped == {"/position/meta": {"value": {"a": 1}}}


# HiBob's create and update payloads type these as numbers, and reject a
# string ID with a bare 400.
NUMERIC_ID_FIELDS = (
    "/position/site",
    "/position/jobProfile",
    "/position/managerPositionId",
)


@pytest.mark.parametrize("field_id", NUMERIC_ID_FIELDS)
@pytest.mark.parametrize("given", ["2555828", " 2555828 ", {"value": "2555828"}])
def test_wrap_fields_sends_numeric_ids_given_as_strings_as_numbers(
    field_id: str, given: object
) -> None:
    assert wrap_fields(OBJECT_TYPE_POSITION, {field_id: given}) == {
        field_id: {"value": 2555828}
    }


@pytest.mark.parametrize("field_id", NUMERIC_ID_FIELDS)
@pytest.mark.parametrize(
    "given",
    ["Berlin - Office", "P-0000000009", "12.5", "-5", "", "\u0663", 12.5, True, [1]],
)
def test_wrap_fields_rejects_a_numeric_id_that_is_not_a_number(
    field_id: str, given: object
) -> None:
    with pytest.raises(ValueError) as excinfo:
        wrap_fields(OBJECT_TYPE_POSITION, {field_id: given})
    assert field_id in str(excinfo.value)
    assert repr(given) in str(excinfo.value)


def test_wrap_fields_keeps_list_ids_hibob_wants_as_strings() -> None:
    """Department and other plain list fields take their string item IDs."""
    wrapped = wrap_fields(
        OBJECT_TYPE_POSITION,
        {"/position/department": "263717557", "/position/field_24023446": "263896351"},
    )
    assert wrapped == {
        "/position/department": {"value": "263717557"},
        "/position/field_24023446": {"value": "263896351"},
    }


# One field of each kind HiBob's API reference types, with its object.
TYPED_FIELDS = [
    (OBJECT_TYPE_POSITION, "/position/site"),
    (OBJECT_TYPE_POSITION, "/position/managerPositionId"),
    (OBJECT_TYPE_POSITION, "/position/fte"),
    (OBJECT_TYPE_BUDGET, "/positionBudget/totalPositionCostCurrencyValue"),
    (OBJECT_TYPE_POSITION, "/position/effectiveDate"),
    (OBJECT_TYPE_POSITION, "/position/department"),
]


@pytest.mark.parametrize("object_type,field_id", TYPED_FIELDS)
def test_wrap_fields_refuses_null_for_a_typed_field(
    object_type: str, field_id: str
) -> None:
    """HiBob allows null only on some creates, where leaving the field out
    does the same, and refuses it on update: a manager cannot be cleared."""
    with pytest.raises(ValueError, match=field_id):
        wrap_fields(object_type, {field_id: None})


AMOUNT_FIELDS = (
    "/positionBudget/expectedBaseSalaryCurrencyValue",
    "/positionBudget/totalPositionCostCurrencyValue",
    "/positionBudget/expectedVariablePayCurrencyValue",
)
NUMBER_FIELDS = [(OBJECT_TYPE_POSITION, "/position/fte")] + [
    (OBJECT_TYPE_BUDGET, field_id) for field_id in AMOUNT_FIELDS
]


@pytest.mark.parametrize("object_type,field_id", NUMBER_FIELDS)
@pytest.mark.parametrize(
    "given,sent",
    [("100000", 100000), (" 80 ", 80), ("65000.5", 65000.5), (65000.5, 65000.5)],
)
def test_wrap_fields_sends_numbers_given_as_strings_as_numbers(
    object_type: str, field_id: str, given: object, sent: float
) -> None:
    assert wrap_fields(object_type, {field_id: given}) == {field_id: {"value": sent}}


@pytest.mark.parametrize("object_type,field_id", NUMBER_FIELDS)
@pytest.mark.parametrize(
    "given", ["100,000", "€100000", "100k", "100%", "1e5", "", True, [100]]
)
def test_wrap_fields_refuses_a_number_that_is_not_one(
    object_type: str, field_id: str, given: object
) -> None:
    with pytest.raises(ValueError) as excinfo:
        wrap_fields(object_type, {field_id: given})
    assert field_id in str(excinfo.value)
    assert repr(given) in str(excinfo.value)


def test_wrap_fields_takes_amounts_from_money_values_in_the_budget_currency() -> None:
    """Budget searches return amounts as {"value": n, "currency": c}, where
    HiBob's writes take the bare number."""
    wrapped = wrap_fields(
        OBJECT_TYPE_BUDGET,
        {
            "currency": "EUR",
            "/positionBudget/expectedBaseSalaryCurrencyValue": {
                "value": 100000,
                "currency": "EUR",
            },
            "/positionBudget/totalPositionCostCurrencyValue": {
                "value": {"value": 120000, "currency": "EUR"},
                "humanReadable": "€120000.00",
            },
        },
    )
    assert wrapped == {
        "/positionBudget/currency": {"value": "EUR"},
        "/positionBudget/expectedBaseSalaryCurrencyValue": {"value": 100000},
        "/positionBudget/totalPositionCostCurrencyValue": {"value": 120000},
    }


@pytest.mark.parametrize("budget_currency", ["GBP", None])
def test_wrap_fields_refuses_a_money_value_not_matched_to_the_budget_currency(
    budget_currency: str | None,
) -> None:
    """Without the budget's currency to compare, a copied amount could be
    written in the wrong currency."""
    fields: dict[str, object] = {
        "/positionBudget/expectedBaseSalaryCurrencyValue": {
            "value": 100000,
            "currency": "EUR",
        }
    }
    if budget_currency:
        fields["/positionBudget/currency"] = budget_currency
    with pytest.raises(
        ValueError, match="/positionBudget/expectedBaseSalaryCurrencyValue"
    ):
        wrap_fields(OBJECT_TYPE_BUDGET, fields)


DATE_FIELDS = [
    (OBJECT_TYPE_POSITION, "/position/effectiveDate"),
    (OBJECT_TYPE_OPENING, "/positionOpening/expectedStartDate"),
]


@pytest.mark.parametrize("object_type,field_id", DATE_FIELDS)
@pytest.mark.parametrize("given", ["2026-09-01", " 2026-09-01 "])
def test_wrap_fields_sends_iso_dates(
    object_type: str, field_id: str, given: str
) -> None:
    assert wrap_fields(object_type, {field_id: given}) == {
        field_id: {"value": "2026-09-01"}
    }


@pytest.mark.parametrize("object_type,field_id", DATE_FIELDS)
@pytest.mark.parametrize(
    "given",
    [
        "01/09/2026",  # how HiBob's search results display dates
        "2026-9-1",
        "2026-02-30",
        "2026-09-01T00:00:00",
        "20260901",
        "",
        20260901,
    ],
)
def test_wrap_fields_refuses_a_date_not_written_yyyy_mm_dd(
    object_type: str, field_id: str, given: object
) -> None:
    with pytest.raises(ValueError) as excinfo:
        wrap_fields(object_type, {field_id: given})
    assert field_id in str(excinfo.value)
    assert repr(given) in str(excinfo.value)


STRING_FIELDS = [
    (OBJECT_TYPE_POSITION, "/position/department"),
    (OBJECT_TYPE_POSITION, "/position/positionType"),
    (OBJECT_TYPE_POSITION, "/position/employmentType"),
    (OBJECT_TYPE_OPENING, "/positionOpening/recruitmentStatus"),
    (OBJECT_TYPE_BUDGET, "/positionBudget/currency"),
    (OBJECT_TYPE_BUDGET, "/positionBudget/salaryPayPeriod"),
    (OBJECT_TYPE_BUDGET, "/positionBudget/variablePayPeriod"),
]


@pytest.mark.parametrize("object_type,field_id", STRING_FIELDS)
def test_wrap_fields_sends_list_ids_given_as_numbers_as_strings(
    object_type: str, field_id: str
) -> None:
    assert wrap_fields(object_type, {field_id: 263717557}) == {
        field_id: {"value": "263717557"}
    }


@pytest.mark.parametrize("object_type,field_id", STRING_FIELDS)
@pytest.mark.parametrize("given", [12.5, True, ["263717557"]])
def test_wrap_fields_refuses_a_list_value_that_is_not_a_string(
    object_type: str, field_id: str, given: object
) -> None:
    with pytest.raises(ValueError) as excinfo:
        wrap_fields(object_type, {field_id: given})
    assert field_id in str(excinfo.value)
    assert repr(given) in str(excinfo.value)


def test_create_position_envelope_matches_hibob_shape() -> None:
    body = build_items_envelope(
        OBJECT_TYPE_POSITION,
        {
            "/position/effectiveDate": "2026-09-01",
            "/position/fte": 100,
            "/position/department": "Engineering",
            "/position/site": 123,
            "/position/jobProfile": 456,
        },
        opening={"/positionOpening/expectedStartDate": "2026-09-30"},
        budget={
            "/positionBudget/salaryPayPeriod": "Annual",
            "/positionBudget/currency": "GBP",
            "/positionBudget/expectedBaseSalaryCurrencyValue": 65000,
        },
    )

    assert body == {
        "items": [
            {
                "objectType": "position",
                "fields": {
                    "/position/effectiveDate": {"value": "2026-09-01"},
                    "/position/fte": {"value": 100},
                    "/position/department": {"value": "Engineering"},
                    "/position/site": {"value": 123},
                    "/position/jobProfile": {"value": 456},
                    "/position/positionOpening": {
                        "objectType": "positionOpening",
                        "fields": {
                            "/positionOpening/expectedStartDate": {
                                "value": "2026-09-30"
                            }
                        },
                    },
                    "/position/positionBudget": {
                        "objectType": "positionBudget",
                        "fields": {
                            "/positionBudget/salaryPayPeriod": {"value": "Annual"},
                            "/positionBudget/currency": {"value": "GBP"},
                            "/positionBudget/expectedBaseSalaryCurrencyValue": {
                                "value": 65000
                            },
                        },
                    },
                },
            }
        ]
    }


def test_envelope_without_nested_objects() -> None:
    body = build_items_envelope(OBJECT_TYPE_OPENING, {"recruitmentStatus": "onHold"})
    assert body == {
        "items": [
            {
                "objectType": "positionOpening",
                "fields": {"/positionOpening/recruitmentStatus": {"value": "onHold"}},
            }
        ]
    }


def test_nested_objects_rejected_outside_position() -> None:
    with pytest.raises(ValueError, match="only be nested inside a position"):
        build_items_envelope(
            OBJECT_TYPE_OPENING, {"recruitmentStatus": "open"}, opening={"a": 1}
        )


def test_validate_required_keys_lists_missing_fields() -> None:
    with pytest.raises(ValueError) as excinfo:
        validate_required_keys(
            OBJECT_TYPE_BUDGET,
            {"/positionBudget/currency": "GBP"},
            {"/positionBudget/salaryPayPeriod", "/positionBudget/currency"},
        )
    message = str(excinfo.value)
    assert "Missing required positionBudget field(s): " in message
    assert "/positionBudget/salaryPayPeriod" in message
    # The supplied field must not be reported as missing.
    assert "/positionBudget/currency" not in message


def test_validate_required_keys_accepts_bare_names() -> None:
    validate_required_keys(
        OBJECT_TYPE_BUDGET,
        {"currency": "GBP", "salaryPayPeriod": "Annual"},
        {"/positionBudget/salaryPayPeriod", "/positionBudget/currency"},
    )


def test_validate_allowed_keys_reports_unknown_field() -> None:
    with pytest.raises(ValueError) as excinfo:
        validate_allowed_keys(
            OBJECT_TYPE_POSITION,
            {"/position/status": "vacant"},
            {"/position/name", "/position/fte"},
        )
    message = str(excinfo.value)
    assert "/position/status" in message
    assert "/position/name" in message


def test_flatten_entry_splits_values_and_display() -> None:
    entry = {
        "/position/name": {"value": "P-1", "humanReadable": "P-1"},
        "/position/status": {"value": "vacant", "humanReadable": "Vacant"},
    }
    assert flatten_search_entry(entry) == {
        "values": {"/position/name": "P-1", "/position/status": "vacant"},
        "display": {"/position/name": "P-1", "/position/status": "Vacant"},
    }


def test_flatten_entry_without_human_readable_omits_display() -> None:
    assert flatten_search_entry({"/position/id": {"value": 7}}) == {
        "values": {"/position/id": 7}
    }


def test_flatten_entry_tolerates_bare_values() -> None:
    assert flatten_search_entry({"/position/id": 7}) == {"values": {"/position/id": 7}}


def test_flatten_entries_ignores_non_list_input() -> None:
    assert flatten_search_entries(None) == []
    assert flatten_search_entries([{"/position/id": {"value": 1}}]) == [
        {"values": {"/position/id": 1}}
    ]
