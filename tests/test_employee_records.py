"""Record types: matching, bodies, masking and comparing rows."""

from __future__ import annotations

import pytest

from hibob_advanced_mcp.employee_records import (
    RECORD_TYPES,
    as_field,
    body_for,
    compare_record,
    custom_record_type,
    describe_record_type,
    find_column,
    find_identical,
    find_new_row,
    find_record_types,
    mask_text,
    masked,
)
from hibob_advanced_mcp.list_values import list_item_names
from hibob_advanced_mcp.people_fields import normalize_custom_tables
from people_data import CUSTOM_TABLES

BY_KEY = {rt.key: rt for rt in RECORD_TYPES}
CERTS = custom_record_type(normalize_custom_tables(CUSTOM_TABLES)[0])
ALL = (*RECORD_TYPES, CERTS)


def test_every_record_type_is_well_formed() -> None:
    assert [rt.key for rt in RECORD_TYPES] == [
        "variable",
        "entitlement",
        "deduction",
        "equity",
        "training",
        "bank_account",
        "dependent",
        "right_to_work",
    ]
    for rt in RECORD_TYPES:
        ids = [c.id for c in rt.columns]
        assert len(ids) == len(set(ids)), rt.key
        assert rt.read in ("table", "bulk")
        for column in rt.columns:
            if column.kind in ("list", "multi-list"):
                assert column.list, (rt.key, column.id)
    assert {rt.key for rt in RECORD_TYPES if rt.dated} == {
        "variable",
        "entitlement",
        "deduction",
    }
    assert {rt.key for rt in RECORD_TYPES if rt.read == "bulk"} == {
        "entitlement",
        "deduction",
        "dependent",
        "right_to_work",
    }


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("Variable pay", "variable"),
        ("  variable   PAY ", "variable"),
        ("variable", "variable"),
        ("entitlement", "entitlement"),
        ("Bank accounts", "bank_account"),
        ("bank-accounts", "bank_account"),
        ("right to work", "right_to_work"),
        ("Dependents", "dependent"),
        ("Stock options", "equity"),
    ],
)
def test_record_types_are_found_by_label_key_alias_or_path(text: str, key: str) -> None:
    assert [rt.key for rt in find_record_types(ALL, text)] == [key]


def test_custom_tables_are_found_by_name_or_id_and_unknown_types_by_nothing() -> None:
    assert find_record_types(ALL, "certifications") == [CERTS]
    assert find_record_types(ALL, "about__table_1") == [CERTS]
    assert find_record_types(ALL, "holidays") == []
    with pytest.raises(ValueError):
        find_record_types(ALL, " ")


def test_columns_are_found_by_label_or_id_ignoring_case() -> None:
    variable = BY_KEY["variable"]
    column = find_column(variable, "payment PERIOD")
    assert column is not None and column.id == "paymentPeriod"
    assert find_column(variable, "amount") is find_column(variable, "Amount")
    assert find_column(variable, "nonsense") is None


def test_a_custom_table_keeps_its_columns_kinds_and_required_flags() -> None:
    assert (CERTS.path, CERTS.custom, CERTS.dated, CERTS.read) == (
        "about__table_1",
        True,
        False,
        "table",
    )
    certificate, expires = CERTS.columns
    assert (certificate.id, certificate.kind, certificate.required) == (
        "column_1",
        "list",
        True,
    )
    assert certificate.list == "certs"
    assert (expires.kind, expires.required) == ("date", False)


def test_body_for_adds_the_date_to_dated_types_and_wraps_custom_tables() -> None:
    values = {"amount": {"value": 1, "currency": "GBP"}}
    assert body_for(BY_KEY["variable"], values, "2030-01-01") == {
        **values,
        "effectiveDate": "2030-01-01",
    }
    assert body_for(BY_KEY["equity"], {"quantity": 5}, None) == {"quantity": 5}
    assert body_for(CERTS, {"column_1": "x"}, None) == {"values": [{"column_1": "x"}]}


def test_sensitive_columns_are_masked_to_their_last_four_characters() -> None:
    bank = BY_KEY["bank_account"]
    row = {
        "bankName": "Acme",
        "accountNumber": "12345678",
        "iban": "GB29NWBK60161331926819",
        "routingNumber": "123",
        "accountNickname": None,
    }
    assert masked(bank, row) == {
        "bankName": "Acme",
        "accountNumber": "****5678",
        "iban": "******************6819",
        "routingNumber": "***",
        "accountNickname": None,
    }
    assert mask_text("1234") == "****"


def test_compare_record_reports_what_differs() -> None:
    sent = {
        "amount": {"value": 5000, "currency": "GBP"},
        "variableType": "Bonus",
        "effectiveDate": "2030-01-01",
    }
    row = {
        "amount": {"value": 5000.0, "currency": "GBP"},
        "variableType": None,
        "effectiveDate": "2030-01-01",
    }
    assert compare_record(sent, row) == [
        {"column": "variableType", "sent": "Bonus", "read": None}
    ]


def test_find_identical_and_find_new_row() -> None:
    sent = {"firstName": "Ada", "surname": "Lovelace"}
    old = {"id": 1, "firstName": "Ada", "surname": "Byron"}
    same = {"id": 2, "firstName": "Ada", "surname": "Lovelace", "gender": None}
    assert find_identical([old], sent) is None
    assert find_identical([old, same], sent) is same
    assert find_new_row([old], [old, same], None, sent) is same
    assert find_new_row([old], [old, same], 2, sent) is same
    assert find_new_row([old], [old], None, sent) is None
    assert find_new_row([old], [old, same], 9, sent) is None
    other = {"id": 3, "firstName": "Bob", "surname": "X"}
    assert find_new_row([old], [old, other, same], None, sent) is same


def test_describe_record_type_lists_columns_with_required_flags() -> None:
    entry = describe_record_type(BY_KEY["entitlement"])
    assert entry["id"] == "entitlement"
    assert entry["dated"] is True
    by_id = {c["id"]: c for c in entry["columns"]}
    assert by_id["entitlement"]["required"] is True
    assert by_id["entitlement"]["list"] == "entitlementType"
    assert by_id["endDate"]["type"] == "date"
    grant = {c["id"]: c for c in describe_record_type(BY_KEY["equity"])["columns"]}
    assert grant["grantType"]["options"] == ["Initial Grant", "Merit Grant"]


def test_as_field_gives_the_value_resolver_the_types_it_knows() -> None:
    variable = BY_KEY["variable"]
    amount = find_column(variable, "amount")
    kind = find_column(variable, "Variable type")
    assert amount is not None and kind is not None
    field = as_field(variable, amount)
    assert (field.type, field.qualified_label) == ("currency", "Variable pay > Amount")
    listed = as_field(variable, kind)
    assert (listed.type, listed.list_id) == ("list", "payType")


def test_list_item_names_maps_ids_to_names_through_a_tree() -> None:
    items = [
        {"id": "ET1", "name": "Lunch vouchers"},
        {
            "id": "G",
            "name": "Group",
            "children": [{"id": "ET2", "name": "Company Car"}],
        },
    ]
    assert list_item_names(items) == {"ET1": "Lunch vouchers", "ET2": "Company Car"}
