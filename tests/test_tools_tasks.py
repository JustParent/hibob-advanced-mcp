"""Tasks tools: open tasks, employee lookup, per-employee tasks, completion."""

from __future__ import annotations

import json

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from conftest import call_tool


async def test_list_open_tasks(mock_api, mcp_server) -> None:
    route = mock_api.get("/tasks").mock(
        return_value=httpx.Response(200, json={"tasks": [{"id": 1, "title": "T"}]})
    )
    text = await call_tool(mcp_server, "hibob_list_open_tasks", {})
    assert route.called
    assert json.loads(text)["tasks"][0]["id"] == 1


async def test_find_employee_returns_name_and_email(mock_api, mcp_server) -> None:
    route = mock_api.post("/people/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "employees": [
                    {"id": "42", "displayName": "Jane Smith", "email": "jane@x.com"}
                ]
            },
        )
    )
    text = await call_tool(mcp_server, "hibob_find_employee", {"email": " Jane@X.com "})
    body = json.loads(route.calls.last.request.content)
    assert body["filters"][0]["values"] == ["jane@x.com"]
    assert json.loads(text) == {
        "count": 1,
        "employees": [{"id": "42", "name": "Jane Smith", "email": "jane@x.com"}],
    }


async def test_find_employee_403_is_explained(mock_api, mcp_server) -> None:
    mock_api.post("/people/search").mock(return_value=httpx.Response(403, json={}))
    text = await call_tool(mcp_server, "hibob_find_employee", {"email": "a@b.com"})
    assert text.startswith("Error:")
    assert "names and email" in text


async def test_get_employee_tasks_with_status(mock_api, mcp_server) -> None:
    route = mock_api.get("/tasks/people/42", params={"task_status": "open"}).mock(
        return_value=httpx.Response(200, json={"tasks": []})
    )
    text = await call_tool(
        mcp_server,
        "hibob_get_employee_tasks",
        {"employee_id": "42", "task_status": "open"},
    )
    assert route.called
    assert json.loads(text) == {"tasks": []}


async def test_get_employee_tasks_403_names_tasks_permission(
    mock_api, mcp_server
) -> None:
    mock_api.get("/tasks/people/42").mock(return_value=httpx.Response(403, json={}))
    text = await call_tool(mcp_server, "hibob_get_employee_tasks", {"employee_id": "42"})
    assert text.startswith("Error:")
    assert "tasks" in text
    assert "Manage positions" not in text


async def test_empty_ids_send_nothing(mock_api, mcp_server) -> None:
    route = mock_api.route().mock(return_value=httpx.Response(200, json={}))
    for name, args in [
        ("hibob_get_employee_tasks", {"employee_id": " "}),
        ("hibob_complete_task", {"task_id": ""}),
        ("hibob_find_employee", {"email": ""}),
    ]:
        assert (await call_tool(mcp_server, name, args)).startswith("Error:")
    assert not route.called


async def test_complete_task_posts_once(mock_api, mcp_server) -> None:
    route = mock_api.post("/tasks/7/complete").mock(return_value=httpx.Response(200))
    text = await call_tool(mcp_server, "hibob_complete_task", {"task_id": "7"})
    assert route.call_count == 1
    assert json.loads(text) == {"task_id": "7", "completed": True}


async def test_complete_task_404_points_at_task_ids(mock_api, mcp_server) -> None:
    mock_api.post("/tasks/9/complete").mock(return_value=httpx.Response(404, json={}))
    text = await call_tool(mcp_server, "hibob_complete_task", {"task_id": "9"})
    assert text.startswith("Error:")
    assert "task or employee ID" in text


async def test_complete_task_absent_in_read_only(server_factory) -> None:
    with pytest.raises(ToolError):
        await call_tool(server_factory(read_only=True), "hibob_complete_task", {"task_id": "1"})
