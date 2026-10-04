"""Report APIs: wire contracts, exact content, polling, and safe downloads."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from conftest import call_tool
from hibob_advanced_mcp import reports
from hibob_advanced_mcp.config import ENV_SERVICE_USER_TOKEN


async def test_list_reports_preserves_permission_filtered_metadata(
    mock_api, mcp_server
):
    payload = {
        "views": [{"id": 123, "name": "Example report", "description": "Details"}]
    }
    route = mock_api.get("/company/reports").respond(200, json=payload)
    assert json.loads(await call_tool(mcp_server, "hibob_list_reports")) == payload
    assert route.call_count == 1


async def test_json_download_preserves_ids_nulls_and_metadata(mock_api, mcp_server):
    payload = {"reportName": "Example", "rows": [{"id": "0012", "value": None}]}
    response = httpx.Response(200, json=payload)
    route = mock_api.get("/company/reports/123/download").mock(return_value=response)
    result = json.loads(
        await call_tool(mcp_server, "hibob_download_report", {"report_id": 123})
    )
    assert dict(route.calls.last.request.url.params) == {
        "format": "json",
        "includeInfo": "true",
    }
    assert result == {
        "status": "ready",
        "format": "json",
        "content_type": "application/json",
        "report_id": 123,
        "data": payload,
        "byte_count": len(response.content),
        "sha256": hashlib.sha256(response.content).hexdigest(),
    }


async def test_download_options_are_query_encoded_and_no_response_is_cached(
    mock_api, mcp_server
):
    route = mock_api.get("/company/reports/123/download").mock(
        side_effect=[
            httpx.Response(200, json=[{"id": "1"}]),
            httpx.Response(200, json=[{"id": "2"}]),
        ]
    )
    args = {
        "report_id": 123,
        "include_info": False,
        "locale": "fr-FR",
        "human_readable": "APPEND",
    }
    first = json.loads(await call_tool(mcp_server, "hibob_download_report", args))
    second = json.loads(await call_tool(mcp_server, "hibob_download_report", args))
    assert first["data"] != second["data"]
    assert dict(route.calls.last.request.url.params) == {
        "format": "json",
        "includeInfo": "false",
        "locale": "fr-FR",
        "humanReadable": "APPEND",
    }


async def test_csv_is_exact_text_including_duplicate_headers_zeroes_and_newlines(
    mock_api, mcp_server
):
    csv = '\ufeffID,Value,Value\r\n0012,"first\nsecond",\r\n'
    mock_api.get("/company/reports/123/download").respond(
        200, text=csv, headers={"Content-Type": "text/csv; charset=utf-8"}
    )
    result = json.loads(
        await call_tool(
            mcp_server, "hibob_download_report", {"report_id": 123, "format": "csv"}
        )
    )
    assert result["text"] == csv
    assert "data" not in result


async def test_xlsx_is_lossless_base64(mock_api, mcp_server):
    content = b"PK\x03\x04\x00\xff\x80example"
    mock_api.get("/company/reports/123/download").respond(
        200,
        content=content,
        headers={
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        },
    )
    result = json.loads(
        await call_tool(
            mcp_server, "hibob_download_report", {"report_id": 123, "format": "xlsx"}
        )
    )
    assert result["encoding"] == "base64"
    assert base64.b64decode(result["data_base64"]) == content
    assert "text" not in result


@pytest.mark.parametrize("status", [200, 202])
@pytest.mark.parametrize(
    "location",
    [
        "https://app.hibob.com/v1/company/reports/download/2026-01-01%20Example.CsvFormat",
        "/v1/company/reports/download/2026-01-01%20Example.CsvFormat",
        "/company/reports/download/2026-01-01%20Example.CsvFormat",
    ],
)
async def test_generate_returns_name_without_fetching_location(
    mock_api, mcp_server, status, location
):
    route = mock_api.get("/company/reports/123/download-async").respond(
        status, headers={"Location": location}
    )
    result = json.loads(
        await call_tool(
            mcp_server,
            "hibob_generate_report",
            {"report_id": 123, "locale": "en-GB", "include_info": False},
        )
    )
    assert result == {
        "status": "accepted",
        "report_id": 123,
        "format": "csv",
        "report_name": "2026-01-01 Example.CsvFormat",
    }
    assert dict(route.calls.last.request.url.params) == {
        "format": "csv",
        "includeInfo": "false",
        "locale": "en-GB",
    }
    assert len(mock_api.calls) == 1


async def test_generated_report_pending_then_ready_without_restarting_generation(
    mock_api, mcp_server
):
    route = mock_api.get("/company/reports/download/Example%20report.CsvFormat").mock(
        side_effect=[
            httpx.Response(204),
            httpx.Response(
                200, text="id,name\n1,Example\n", headers={"Content-Type": "text/csv"}
            ),
        ]
    )
    args = {"report_name": "Example report.CsvFormat"}
    first = json.loads(
        await call_tool(mcp_server, "hibob_download_generated_report", args)
    )
    assert first == {"status": "pending", "format": "csv", **args}
    second = json.loads(
        await call_tool(mcp_server, "hibob_download_generated_report", args)
    )
    assert second["status"] == "ready"
    assert second["text"] == "id,name\n1,Example\n"
    assert route.call_count == len(mock_api.calls) == 2


@pytest.mark.parametrize("status", [429, 503])
async def test_generation_is_never_retried(
    mock_api, mcp_server, recorded_sleeps, status
):
    route = mock_api.get("/company/reports/123/download-async").respond(status, json={})
    result = await call_tool(mcp_server, "hibob_generate_report", {"report_id": 123})
    assert result.startswith("Error:")
    assert route.call_count == 1
    assert recorded_sleeps == []


async def test_download_retries_rate_limit_and_preserves_response_headers(
    mock_api, mcp_server, recorded_sleeps
):
    route = mock_api.get("/company/reports/123/download").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(200, json=[]),
        ]
    )
    result = json.loads(
        await call_tool(mcp_server, "hibob_download_report", {"report_id": 123})
    )
    assert result["status"] == "ready"
    assert result["content_type"] == "application/json"
    assert route.call_count == 2
    assert recorded_sleeps == [3.0]


@pytest.mark.parametrize(
    "tool,args",
    [
        ("hibob_list_reports", {}),
        ("hibob_download_report", {"report_id": 123}),
        ("hibob_generate_report", {"report_id": 123}),
        ("hibob_download_generated_report", {"report_name": "Example.CsvFormat"}),
    ],
)
@pytest.mark.parametrize(
    "status,expected",
    [
        (400, "Configure report filters"),
        (401, "credentials"),
        (403, "Features > Reports"),
        (404, "hibob_list_reports"),
        (429, "reports: 20/min"),
    ],
)
async def test_errors_give_report_specific_guidance(
    mock_api, mcp_server, tool, args, status, expected
):
    mock_api.route().respond(status, json={})
    text = await call_tool(mcp_server, tool, args)
    assert text.startswith("Error:")
    assert expected in text
    assert "Manage positions" not in text


async def test_401_permission_error_names_reports_permission(mock_api, mcp_server):
    mock_api.get("/company/reports").respond(
        401, json={"errorMessage": "No permissions to reports"}
    )
    assert "Features > Reports" in await call_tool(mcp_server, "hibob_list_reports")


@pytest.mark.parametrize(
    "tool,args",
    [
        ("hibob_download_report", {"report_id": 0}),
        ("hibob_download_report", {"report_id": -1}),
        ("hibob_download_report", {"report_id": True}),
        ("hibob_download_report", {"report_id": "123/other"}),
        ("hibob_download_report", {"report_id": 123, "format": "pdf"}),
        ("hibob_generate_report", {"report_id": 123, "format": "json"}),
    ],
)
async def test_schema_rejects_invalid_ids_and_unsupported_formats(
    mock_api, mcp_server, tool, args
):
    with pytest.raises(ToolError):
        await call_tool(mcp_server, tool, args)
    assert not mock_api.calls


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"locale": " "}, "locale"),
        ({"format": "csv", "human_readable": "APPEND"}, "only for JSON"),
    ],
)
async def test_invalid_query_options_send_nothing(mock_api, mcp_server, args, expected):
    result = await call_tool(
        mcp_server, "hibob_download_report", {"report_id": 123, **args}
    )
    assert result.startswith("Error:") and expected in result
    assert not mock_api.calls


@pytest.mark.parametrize(
    "name",
    [
        "",
        " ",
        ".",
        "..",
        "../report.csv",
        "https://evil.example/a",
        "x/y",
        "x\\y",
        "%2e%2e",
        "x?token=y",
        "x#y",
        "x\n",
        "x\x7f",
        "x" * 1025,
    ],
)
async def test_download_refuses_urls_traversal_and_encoded_paths(
    mock_api, mcp_server, name
):
    result = await call_tool(
        mcp_server, "hibob_download_generated_report", {"report_name": name}
    )
    assert result.startswith("Error:")
    assert not mock_api.calls


@pytest.mark.parametrize(
    "location",
    [
        None,
        "https://example.com/other",
        "/company/reports/download/a?token=x",
        "/company/reports/download/a#x",
        "/company/reports/download/%2e%2e",
        "/company/reports/download/a%2fb",
    ],
)
async def test_generation_refuses_missing_or_invalid_location(
    mock_api, mcp_server, location
):
    headers = {"Location": location} if location is not None else {}
    mock_api.get("/company/reports/123/download-async").respond(202, headers=headers)
    result = await call_tool(mcp_server, "hibob_generate_report", {"report_id": 123})
    assert result.startswith("Error:")
    assert len(mock_api.calls) == 1


async def test_location_host_cannot_redirect_credentials(mock_api, mcp_server):
    mock_api.get("/company/reports/123/download-async").respond(
        202,
        headers={
            "Location": "https://evil.example/v1/company/reports/download/report.CsvFormat"
        },
    )
    route = mock_api.get("/company/reports/download/report.CsvFormat").respond(
        200, text="id\n1\n", headers={"Content-Type": "text/csv"}
    )
    generated = json.loads(
        await call_tool(mcp_server, "hibob_generate_report", {"report_id": 123})
    )
    await call_tool(
        mcp_server,
        "hibob_download_generated_report",
        {"report_name": generated["report_name"]},
    )
    assert route.called
    assert all(call.request.url.host == "api.hibob.com" for call in mock_api.calls)


@pytest.mark.parametrize(
    "tool,args,path,status",
    [
        ("hibob_list_reports", {}, "/company/reports", 204),
        (
            "hibob_generate_report",
            {"report_id": 123},
            "/company/reports/123/download-async",
            204,
        ),
        (
            "hibob_download_report",
            {"report_id": 123},
            "/company/reports/123/download",
            302,
        ),
    ],
)
async def test_unexpected_success_or_redirect_is_not_report_data(
    mock_api, mcp_server, tool, args, path, status
):
    mock_api.get(path).respond(status, headers={"Location": "https://evil.example"})
    result = await call_tool(mcp_server, tool, args)
    assert result.startswith("Error:")
    assert len(mock_api.calls) == 1


@pytest.mark.parametrize(
    "format,content,content_type,expected",
    [
        ("json", b"<html>login</html>", "text/html", "valid JSON"),
        ("csv", b"<html>login</html>", "text/html", "content type"),
        ("csv", b"\xff", "text/csv; charset=utf-8", "valid text"),
        ("xlsx", b"<html>login</html>", "text/html", "XLSX"),
    ],
)
async def test_invalid_file_content_is_not_treated_as_a_ready_report(
    mock_api, mcp_server, format, content, content_type, expected
):
    mock_api.get("/company/reports/123/download").respond(
        200, content=content, headers={"Content-Type": content_type}
    )
    result = await call_tool(
        mcp_server, "hibob_download_report", {"report_id": 123, "format": format}
    )
    assert result.startswith("Error:") and expected in result


class Chunks(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"1234"
        yield b"5678"


async def test_size_limit_is_enforced_without_content_length(
    mock_api, mcp_server, monkeypatch
):
    monkeypatch.setattr(reports, "MAX_REPORT_BYTES", 6)
    mock_api.get("/company/reports/123/download").mock(
        return_value=httpx.Response(200, stream=Chunks())
    )
    result = await call_tool(
        mcp_server, "hibob_download_report", {"report_id": 123, "format": "csv"}
    )
    assert result.startswith("Error:") and "no partial data" in result
    assert "1234" not in result


@pytest.mark.parametrize("limit,expected", [(100, "ready"), (5, "Error:")])
async def test_size_limit_counts_decompressed_bytes(
    mock_api, mcp_server, monkeypatch, limit, expected
):
    monkeypatch.setattr(reports, "MAX_REPORT_BYTES", limit)
    mock_api.get("/company/reports/123/download").respond(
        200,
        content=gzip.compress(b'{"rows":[]}'),
        headers={"Content-Encoding": "gzip", "Content-Type": "application/json"},
    )
    result = await call_tool(mcp_server, "hibob_download_report", {"report_id": 123})
    assert expected in result
    if expected == "ready":
        assert json.loads(result)["data"] == {"rows": []}


async def test_missing_credentials_are_reported_before_network(
    mock_api, mcp_server, client, monkeypatch
):
    monkeypatch.delenv(ENV_SERVICE_USER_TOKEN)
    from mcp.server.fastmcp import FastMCP

    from hibob_advanced_mcp.client import HiBobClient
    from hibob_advanced_mcp.reports import register_reports_tools

    server = FastMCP("unconfigured")
    register_reports_tools(server, client_factory=HiBobClient)
    assert "credentials are not configured" in await call_tool(
        server, "hibob_list_reports"
    )
    assert not mock_api.calls


async def test_timeout_returns_actionable_error(mock_api, mcp_server):
    mock_api.get("/company/reports/123/download").mock(
        side_effect=httpx.ReadTimeout("timeout")
    )
    assert "timed out" in await call_tool(
        mcp_server, "hibob_download_report", {"report_id": 123}
    )


async def test_reports_work_in_read_only_mode_and_generation_is_not_idempotent(
    mock_api, server_factory
):
    server = server_factory(read_only=True)
    mock_api.get("/company/reports").respond(200, json={"views": []})
    assert json.loads(await call_tool(server, "hibob_list_reports")) == {"views": []}
    tools = {tool.name: tool for tool in await server.list_tools()}
    for name in (
        "hibob_list_reports",
        "hibob_download_report",
        "hibob_generate_report",
        "hibob_download_generated_report",
    ):
        assert tools[name].annotations.readOnlyHint
        assert not tools[name].annotations.destructiveHint
        assert tools[name].annotations.idempotentHint is (
            name != "hibob_generate_report"
        )


async def test_invalid_response_limit_fails_before_network(client, mock_api):
    with pytest.raises(ValueError, match="positive"):
        await client.request_response("GET", "/company/reports", max_bytes=0)
    assert not mock_api.calls
