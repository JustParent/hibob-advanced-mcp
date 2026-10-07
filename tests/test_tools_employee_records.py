"""hibob_add_employee_record: one new row in a table that holds several."""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from conftest import call_tool
from people_data import EMPLOYEE_ID, FakePeople


async def _add(mcp_server, **arguments) -> str:
    return await call_tool(mcp_server, "hibob_add_employee_record", arguments)


VARIABLE = {
    "Variable type": "bonus",
    "Amount": {"value": 5000, "currency": "gbp"},
    "Payment period": "annual",
}


async def test_variable_pay_is_added_by_labels_and_read_back(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee="jane@x.com",
            record_type="Variable pay",
            values=VARIABLE,
            effective_date="2030-01-01",
        )
    )
    expected = {
        "variableType": "Bonus",
        "amount": {"value": 5000, "currency": "GBP"},
        "paymentPeriod": "Annual",
        "effectiveDate": "2030-01-01",
    }
    assert fake.posted == [("variable", expected)]
    assert result["status"] == "added"
    assert result["record_type"] == "Variable pay"
    assert result["employee"]["id"] == EMPLOYEE_ID
    assert result["entry_id"] == fake.records_by_path["variable"][0]["id"]
    assert result["row"] == expected
    assert result["verified"] is True


async def test_missing_required_columns_come_back_as_a_question(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="variable pay",
            values={"Amount": {"value": 1, "currency": "GBP"}},
            effective_date="2030-01-01",
        )
    )
    assert result["status"] == "needs_input"
    [question] = result["questions"]
    assert question["argument"] == "values"
    missing = {m["column"]: m for m in question["missing"]}
    assert set(missing) == {"variableType", "paymentPeriod"}
    assert "Bonus" in missing["variableType"]["options"]
    assert fake.writes == []


async def test_a_dated_type_asks_for_the_date_and_an_undated_one_refuses_it(
    mock_api, mcp_server
):
    fake = FakePeople(mock_api)
    asked = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="variable pay",
            values=VARIABLE,
        )
    )
    assert asked["status"] == "needs_input"
    assert asked["questions"][0]["argument"] == "effective_date"
    text = await _add(
        mcp_server,
        employee=EMPLOYEE_ID,
        record_type="training",
        values={"Training name": "Training"},
        effective_date="2030-01-01",
    )
    assert text.startswith("Error:")
    assert "no effective date" in text
    assert "Nothing was written" in text
    assert fake.writes == []


async def test_entitlement_is_sent_by_the_name_not_the_id(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="Entitlement",
            values={
                "Entitlement type": "ET1",
                "Amount": {"value": 150, "currency": "GBP"},
            },
            effective_date="2030-01-01",
        )
    )
    assert fake.posted[0][1]["entitlement"] == "Lunch vouchers"
    assert result["status"] == "added"
    assert result["verified"] is True


async def test_an_identical_record_is_not_added_twice(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    arguments = dict(
        employee=EMPLOYEE_ID,
        record_type="Deduction",
        values={
            "Deduction type": "Company Car",
            "Amount": {"value": 70, "currency": "GBP"},
        },
        effective_date="2030-01-01",
    )
    first = json.loads(await _add(mcp_server, **arguments))
    second = json.loads(await _add(mcp_server, **arguments))
    assert first["status"] == "added"
    assert second["status"] == "unchanged"
    assert "already has" in second["warnings"][0]
    assert second["entry_id"] == first["entry_id"]
    assert fake.writes == ["record:deduction"]


async def test_a_duplicate_hibob_refuses_says_records_are_only_added(
    mock_api, mcp_server
):
    fake = FakePeople(mock_api)
    fake.add_record(
        "deduction",
        effectiveDate="2030-01-01",
        deduction="Company Car",
        amount={"value": 1, "currency": "GBP"},
    )
    text = await _add(
        mcp_server,
        employee=EMPLOYEE_ID,
        record_type="Deduction",
        values={
            "Deduction type": "Company Car",
            "Amount": {"value": 70, "currency": "GBP"},
        },
        effective_date="2030-01-01",
    )
    assert text.startswith("Error:")
    assert "Duplicate effective date" in text
    assert "only adds rows" in text


async def test_equity_is_undated_with_a_fixed_vocabulary(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="equity",
            values={
                "Quantity": "100",
                "Equity type": "Options",
                "Grant type": "merit grant",
                "Grant date": "2026-01-15",
            },
        )
    )
    assert fake.posted == [
        (
            "equities",
            {
                "quantity": 100,
                "equityType": "Options",
                "grantType": "Merit Grant",
                "grantDate": "2026-01-15",
            },
        )
    ]
    assert result["status"] == "added"
    assert result["entry_id"] == fake.records_by_path["equities"][0]["id"]
    assert result["verified"] is True
    bad = await _add(
        mcp_server,
        employee=EMPLOYEE_ID,
        record_type="equity",
        values={"Quantity": 1, "Equity type": "Options", "Grant type": "Gift"},
    )
    assert bad.startswith("Error:") and "Initial Grant" in bad


async def test_training_resolves_its_lists_and_amount(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    await _add(
        mcp_server,
        employee=EMPLOYEE_ID,
        record_type="training",
        values={
            "Training name": "training",
            "Status": "completed",
            "Frequency": "yearly",
            "Cost": {"value": 200, "currency": "GBP"},
        },
    )
    assert fake.posted == [
        (
            "training",
            {
                "name": "Training",
                "status": "Completed",
                "frequency": "Yearly",
                "cost": {"value": 200, "currency": "GBP"},
            },
        )
    ]


async def test_a_dependent_returns_its_entry_id(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="dependent",
            values={
                "First name": "Ada",
                "Surname": "Smith",
                "Birth date": "2015-04-01",
                "Gender": "female",
            },
        )
    )
    assert fake.posted[0][1] == {
        "firstName": "Ada",
        "surname": "Smith",
        "birthDate": "2015-04-01",
        "gender": "Female",
    }
    assert result["entry_id"] == fake.records_by_path["dependents"][0]["id"]
    assert result["verified"] is True


async def test_bank_details_are_sent_in_full_but_never_shown(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="bank account",
            values={
                "Bank name": "Acme",
                "Account number": "12345678",
                "IBAN": "GB29NWBK60161331926819",
                "Account type": "savings",
                "Use for bonus": "yes",
            },
        )
    )
    sent = fake.posted[0][1]
    assert sent["accountNumber"] == "12345678"
    assert sent["iban"] == "GB29NWBK60161331926819"
    assert sent["bankAccountType"] == "Savings"
    assert sent["useForBonus"] is True
    assert result["row"]["accountNumber"] == "****5678"
    assert result["row"]["iban"] == "******************6819"
    text = json.dumps(result)
    assert "12345678" not in text.replace("****5678", "")
    assert "GB29NWBK60161331926819" not in text


async def test_a_dropped_sensitive_column_is_reported_without_its_value(
    mock_api, mcp_server
):
    fake = FakePeople(mock_api)
    fake.drop_on_write["bank-accounts"] = {"accountNumber"}
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="bank account",
            values={"Bank name": "Acme", "Account number": "12345678"},
        )
    )
    assert result["unconfirmed"] == [
        {"field": "Bank account > accountNumber", "sent": "****5678", "read": None}
    ]
    assert "12345678" not in json.dumps(result).replace("****5678", "")


async def test_right_to_work_numbers_are_masked(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="right to work",
            values={
                "Document type": "Visa",
                "Document number": "AB1234567",
                "Expiration date": "2030-05-01",
            },
        )
    )
    assert fake.posted[0][1]["number"] == "AB1234567"
    assert result["row"]["number"] == "*****4567"


async def test_a_custom_table_is_added_by_name_with_its_mandatory_column(
    mock_api, mcp_server
):
    fake = FakePeople(mock_api)
    asked = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="certifications",
            values={"Expires": "2030-01-01"},
        )
    )
    assert asked["status"] == "needs_input"
    assert asked["questions"][0]["missing"][0]["column"] == "column_1"
    assert fake.writes == []
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="Certifications",
            values={"Certificate": "first aid", "Expires": "2030-01-01"},
        )
    )
    assert fake.posted == [
        ("about__table_1", {"values": [{"column_1": "C1", "column_2": "2030-01-01"}]})
    ]
    assert result["status"] == "added"
    assert result["verified"] is True


async def test_unknown_record_types_and_columns_are_refused_or_asked(
    mock_api, mcp_server
):
    fake = FakePeople(mock_api)
    text = await _add(
        mcp_server, employee=EMPLOYEE_ID, record_type="holidays", values={"x": 1}
    )
    assert text.startswith("Error:")
    assert "Variable pay" in text and "Certifications" in text
    asked = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="training",
            values={"Training name": "Training", "Colour": "red"},
        )
    )
    assert asked["status"] == "needs_input"
    assert asked["questions"][0]["key"] == "Colour"
    assert fake.writes == []


async def test_an_ambiguous_employee_is_a_question(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee="Alex Lee",
            record_type="training",
            values={"Training name": "Training"},
        )
    )
    assert result["status"] == "needs_input"
    assert result["questions"][0]["argument"] == "employee"
    assert fake.writes == []


async def test_a_bare_amount_asks_for_its_currency(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="training",
            values={"Training name": "Training", "Cost": 200},
        )
    )
    assert result["status"] == "needs_input"
    assert "currency" in result["questions"][0]["question"]
    assert fake.writes == []


async def test_a_column_given_twice_is_refused(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    text = await _add(
        mcp_server,
        employee=EMPLOYEE_ID,
        record_type="training",
        values={"Training name": "Training", "name": "Training"},
    )
    assert text.startswith("Error:") and "given twice" in text
    assert fake.writes == []


async def test_a_column_hibob_drops_is_reported_unconfirmed(
    mock_api, mcp_server, recorded_sleeps
):
    fake = FakePeople(mock_api)
    fake.drop_on_write["training"] = {"status"}
    result = json.loads(
        await _add(
            mcp_server,
            employee=EMPLOYEE_ID,
            record_type="training",
            values={"Training name": "Training", "Status": "completed"},
        )
    )
    assert result["status"] == "added"
    assert result["unconfirmed"] == [
        {"field": "Training > status", "sent": "Completed", "read": None}
    ]
    assert "verified" not in result or result["verified"] is False
    assert recorded_sleeps == [1.0, 3.0, 6.0]


async def test_a_denied_write_names_the_table_to_grant(mock_api, mcp_server):
    fake = FakePeople(mock_api)
    fake.row_status["training"] = 403
    text = await _add(
        mcp_server,
        employee=EMPLOYEE_ID,
        record_type="training",
        values={"Training name": "Training"},
    )
    assert text.startswith("Error:")
    assert "Training" in text and "Edit" in text


async def test_the_tool_is_absent_in_read_only_mode(server_factory):
    with pytest.raises(ToolError, match="Unknown tool"):
        await call_tool(
            server_factory(read_only=True),
            "hibob_add_employee_record",
            {"employee": EMPLOYEE_ID, "record_type": "training", "values": {"x": 1}},
        )
