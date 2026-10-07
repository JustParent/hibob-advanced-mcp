"""hibob_list_employee_fields and hibob_get_employee."""

from __future__ import annotations

import json

import httpx

from conftest import call_tool
from people_data import EMPLOYEE_ID, FakePeople


async def test_list_fields_says_how_each_is_written(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    result = json.loads(await call_tool(mcp_server, "hibob_list_employee_fields", {}))
    by_id = {field["id"]: field for field in result["fields"]}
    assert by_id["work.title"]["write"] == "dated"
    assert by_id["work.title"]["table"] == "work"
    assert by_id["home.mobilePhone"]["write"] == "field"
    assert by_id["root.email"]["write"] == "email"
    assert by_id["address.city"]["write"] == "not_writable"
    assert result["custom_tables"][0]["name"] == "Certifications"


async def test_list_fields_search_narrows_by_label_id_or_category(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await call_tool(mcp_server, "hibob_list_employee_fields", {"search": "start"})
    )
    assert {field["id"] for field in result["fields"]} == {
        "work.startDate",
        "home.custom.field_200",
    }
    assert result["count"] == 2
    assert result["custom_tables"] == []


async def test_list_fields_survives_custom_tables_being_denied(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    mock_api.get("/people/custom-tables/metadata").mock(
        return_value=httpx.Response(403, json={})
    )
    result = json.loads(await call_tool(mcp_server, "hibob_list_employee_fields", {}))
    assert result["fields"]
    assert "403" in result["custom_tables_error"]


async def test_list_fields_is_available_in_read_only_mode(
    mock_api, server_factory
) -> None:
    FakePeople(mock_api)
    text = await call_tool(
        server_factory(read_only=True), "hibob_list_employee_fields", {}
    )
    assert not text.startswith("Error:")


async def test_get_employee_default_fields_with_display_labels(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await call_tool(mcp_server, "hibob_get_employee", {"employee": "jane@x.com"})
    )
    assert result["employee"]["id"] == EMPLOYEE_ID
    by_id = {entry["id"]: entry for entry in result["fields"]}
    assert by_id["work.title"]["value"] == "101"
    assert by_id["work.title"]["display"] == "Analyst"
    assert by_id["work.title"]["field"] == "Work > Job title"
    assert by_id["work.startDate"]["value"] == "2024-03-01"
    assert "work.siteId" in by_id
    assert "work.site" not in by_id


async def test_get_employee_named_fields_by_label(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_get_employee",
            {"employee": "Jane Smith", "fields": ["Mobile phone", "Shirt size"]},
        )
    )
    assert [(e["id"], e["value"], e["display"]) for e in result["fields"]] == [
        ("home.mobilePhone", "07700 900000", None),
        ("work.custom.field_100", "L", "Large"),
    ]


async def test_get_employee_unknown_or_ambiguous_field_is_an_error(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    text = await call_tool(
        mcp_server,
        "hibob_get_employee",
        {"employee": EMPLOYEE_ID, "fields": ["Start date", "Job titel"]},
    )
    assert text.startswith("Error:")
    assert "Home > Start date" in text
    assert "Job titel" in text


async def test_get_employee_ambiguous_name_lists_candidates(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    text = await call_tool(mcp_server, "hibob_get_employee", {"employee": "Alex Lee"})
    assert text.startswith("Error:")
    assert "alex.lee2@x.com" in text


async def test_get_employee_history_reads_tables_and_reports_per_table_errors(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    mock_api.get(f"/people/{EMPLOYEE_ID}/work").mock(
        return_value=httpx.Response(
            200, json={"values": [{"id": 1, "effectiveDate": "2024-03-01"}]}
        )
    )
    mock_api.get(f"/people/custom-tables/{EMPLOYEE_ID}/about__table_1").mock(
        return_value=httpx.Response(200, json={"values": [{"id": 5, "column_1": "x"}]})
    )
    mock_api.get(f"/people/{EMPLOYEE_ID}/salaries").mock(
        return_value=httpx.Response(403, json={})
    )
    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_get_employee",
            {
                "employee": EMPLOYEE_ID,
                "fields": ["Job title"],
                "history": ["Work", "Certifications", "salary", "Holidays"],
            },
        )
    )
    history = result["history"]
    assert history["Work"]["rows"][0]["id"] == 1
    assert history["Certifications"]["rows"][0]["column_1"] == "x"
    assert "People's fields" in history["salary"]["error"]
    assert "Holidays" in history["Holidays"]["error"]


async def test_list_fields_lists_the_record_types_and_custom_tables(
    mock_api, mcp_server
) -> None:
    FakePeople(mock_api)
    result = json.loads(await call_tool(mcp_server, "hibob_list_employee_fields", {}))
    by_id = {entry["id"]: entry for entry in result["record_types"]}
    assert by_id["variable"]["dated"] is True
    assert {c["id"] for c in by_id["variable"]["columns"] if c["required"]} == {
        "variableType",
        "amount",
        "paymentPeriod",
    }
    assert by_id["about__table_1"]["label"] == "Certifications"
    assert by_id["about__table_1"]["columns"][0]["required"] is True


async def test_list_fields_search_narrows_record_types(mock_api, mcp_server) -> None:
    FakePeople(mock_api)
    result = json.loads(
        await call_tool(mcp_server, "hibob_list_employee_fields", {"search": "bonus"})
    )
    assert [entry["id"] for entry in result["record_types"]] == ["bank_account"]
    result = json.loads(
        await call_tool(mcp_server, "hibob_list_employee_fields", {"search": "certif"})
    )
    assert [entry["id"] for entry in result["record_types"]] == ["about__table_1"]


async def test_history_shows_bulk_only_records_and_masks_bank_numbers(
    mock_api, mcp_server
) -> None:
    fake = FakePeople(mock_api)
    fake.add_record(
        "entitlement", effectiveDate="2026-01-01", entitlement="Company Car"
    )
    fake.add_record("bank-accounts", bankName="Acme", accountNumber="12345678")
    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_get_employee",
            {
                "employee": EMPLOYEE_ID,
                "fields": ["Job title"],
                "history": ["entitlement", "bank accounts", "right to work"],
            },
        )
    )
    history = result["history"]
    assert history["entitlement"]["rows"][0]["entitlement"] == "Company Car"
    assert history["bank accounts"]["rows"][0]["accountNumber"] == "****5678"
    assert history["bank accounts"]["rows"][0]["bankName"] == "Acme"
    assert history["right to work"]["rows"] == []
