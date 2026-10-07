"""hibob_list_employee_fields and hibob_get_employee."""

from __future__ import annotations

import json

import httpx

from conftest import call_tool
from people_data import FakePeople


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
